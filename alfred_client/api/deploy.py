"""Deployment engine for applying changesets to the Frappe site.

Receives approved changesets and executes them step-by-step, creating
DocTypes, Server Scripts, Client Scripts, Workflows, etc. Supports
rollback on failure and audit logging.

This module is called via `POST /api/method/alfred_client.api.deploy.apply_changeset`
from the Deployer Agent via the custom WebSocket channel.
"""

import json
import traceback

import frappe
from frappe import _


@frappe.whitelist()
def apply_changeset(changeset_name):
	"""Apply an approved changeset to the site.

	Args:
		changeset_name: Name of the Alfred Changeset document.

	Returns:
		Dict with execution results.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	changeset = frappe.get_doc("Alfred Changeset", changeset_name)

	if changeset.status != "Approved":
		frappe.throw(_("Changeset must be approved before deployment. Current status: {0}").format(changeset.status))

	changes = json.loads(changeset.changes) if isinstance(changeset.changes, str) else changeset.changes
	if not changes:
		return {"status": "success", "message": "No changes to deploy", "steps": []}

	execution_log = []
	rollback_data = []
	failed = False

	for i, change in enumerate(changes):
		step = i + 1
		operation = change.get("op", change.get("operation", "create"))
		doctype = change.get("doctype", "")
		data = change.get("data", {})
		doc_name = data.get("name", "")

		# Publish progress
		frappe.publish_realtime(
			"intern_deploy_progress",
			{
				"changeset": changeset_name,
				"step": step,
				"total": len(changes),
				"operation": operation,
				"doctype": doctype,
				"name": doc_name,
				"status": "in_progress",
			},
			user=frappe.session.user,
		)

		try:
			if operation == "create":
				result = _create_document(doctype, data)
				rollback_data.append({
					"operation": "delete",
					"doctype": doctype,
					"name": result.get("name", doc_name),
				})
			elif operation == "update":
				before_state = _get_document_state(doctype, doc_name)
				result = _update_document(doctype, data)
				rollback_data.append({
					"operation": "restore",
					"doctype": doctype,
					"name": doc_name,
					"before_state": before_state,
				})
			elif operation == "delete":
				before_state = _get_document_state(doctype, doc_name)
				result = _delete_document(doctype, doc_name)
				rollback_data.append({
					"operation": "create",
					"doctype": doctype,
					"data": before_state,
				})
			else:
				result = {"error": f"Unknown operation: {operation}"}
				failed = True

			# Audit log
			_write_audit_log(changeset.conversation, doctype, doc_name, operation, result)

			execution_log.append({
				"step": step,
				"operation": operation,
				"doctype": doctype,
				"name": doc_name,
				"status": "success",
			})

			# Publish success
			frappe.publish_realtime(
				"intern_deploy_progress",
				{"changeset": changeset_name, "step": step, "total": len(changes), "status": "success", "name": doc_name},
				user=frappe.session.user,
			)

		except Exception as e:
			error_msg = str(e)
			execution_log.append({
				"step": step,
				"operation": operation,
				"doctype": doctype,
				"name": doc_name,
				"status": "failed",
				"error": error_msg,
			})

			frappe.publish_realtime(
				"intern_deploy_failed",
				{
					"changeset": changeset_name,
					"step": step,
					"error": error_msg,
					"rollback_initiated": True,
				},
				user=frappe.session.user,
			)

			failed = True
			break

	# Update changeset status and log
	if failed:
		# Attempt rollback
		rollback_log = _execute_rollback(rollback_data, changeset.conversation)
		changeset.status = "Rolled Back"
		changeset.deployment_log = json.dumps(execution_log + rollback_log, indent=2)
	else:
		changeset.status = "Deployed"
		changeset.deployment_log = json.dumps(execution_log, indent=2)

	changeset.rollback_data = json.dumps(rollback_data)
	changeset.save(ignore_permissions=True)
	frappe.db.commit()

	if not failed:
		frappe.publish_realtime(
			"intern_deploy_complete",
			{
				"changeset": changeset_name,
				"status": "success",
				"steps": len(execution_log),
			},
			user=frappe.session.user,
		)

	return {
		"status": "success" if not failed else "failed",
		"execution_log": execution_log,
		"rollback_data": rollback_data,
	}


def _create_document(doctype, data):
	"""Create a Frappe document from the changeset data."""
	doc_data = dict(data)
	doc_data["doctype"] = doctype

	doc = frappe.get_doc(doc_data)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return {"name": doc.name, "created": True}


def _update_document(doctype, data):
	"""Update an existing Frappe document."""
	doc_name = data.get("name")
	if not doc_name:
		raise ValueError("Document name is required for update operation")

	doc = frappe.get_doc(doctype, doc_name)
	doc.update(data)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"name": doc.name, "updated": True}


def _delete_document(doctype, name):
	"""Delete a Frappe document."""
	frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
	frappe.db.commit()
	return {"name": name, "deleted": True}


def _get_document_state(doctype, name):
	"""Get the current state of a document for rollback."""
	try:
		doc = frappe.get_doc(doctype, name)
		return doc.as_dict()
	except frappe.DoesNotExistError:
		return None


def _execute_rollback(rollback_data, conversation_name):
	"""Execute rollback operations in reverse order."""
	rollback_log = []
	for item in reversed(rollback_data):
		try:
			op = item.get("operation")
			if op == "delete":
				# Check for records before deleting
				dt = item.get("doctype")
				name = item.get("name")
				if dt == "DocType":
					try:
						count = frappe.db.count(name)
						if count > 0:
							rollback_log.append({
								"operation": "skip_delete",
								"doctype": dt,
								"name": name,
								"reason": f"DocType '{name}' has {count} records — skipping deletion to preserve data",
							})
							continue
					except Exception:
						pass
				frappe.delete_doc(dt, name, ignore_permissions=True, force=True)
				rollback_log.append({"operation": "delete", "doctype": dt, "name": name, "status": "success"})
			elif op == "restore":
				before = item.get("before_state")
				if before:
					doc = frappe.get_doc(item["doctype"], item["name"])
					doc.update(before)
					doc.save(ignore_permissions=True)
					rollback_log.append({"operation": "restore", "doctype": item["doctype"], "name": item["name"], "status": "success"})
			elif op == "create":
				data = item.get("data")
				if data:
					doc = frappe.get_doc(data)
					doc.insert(ignore_permissions=True)
					rollback_log.append({"operation": "recreate", "doctype": item["doctype"], "status": "success"})

			frappe.db.commit()
		except Exception as e:
			rollback_log.append({
				"operation": item.get("operation"),
				"doctype": item.get("doctype"),
				"name": item.get("name"),
				"status": "failed",
				"error": str(e),
			})

	return rollback_log


def _write_audit_log(conversation, doctype, name, operation, result):
	"""Write an entry to the Alfred Audit Log."""
	try:
		frappe.get_doc({
			"doctype": "Alfred Audit Log",
			"conversation": conversation,
			"agent": "deployer",
			"action": f"{operation} {doctype}",
			"document_type": doctype,
			"document_name": name,
			"after_state": json.dumps(result),
		}).insert(ignore_permissions=True)
	except Exception:
		pass  # Don't let audit log failures block deployment


@frappe.whitelist()
def rollback_changeset(changeset_name):
	"""Manually rollback a deployed changeset.

	Args:
		changeset_name: Name of the Alfred Changeset document.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	changeset = frappe.get_doc("Alfred Changeset", changeset_name)
	if changeset.status != "Deployed":
		frappe.throw(_("Can only rollback deployed changesets. Current status: {0}").format(changeset.status))

	rollback_data = json.loads(changeset.rollback_data) if changeset.rollback_data else []
	if not rollback_data:
		frappe.throw(_("No rollback data available for this changeset"))

	rollback_log = _execute_rollback(rollback_data, changeset.conversation)

	changeset.status = "Rolled Back"
	changeset.deployment_log = json.dumps(
		json.loads(changeset.deployment_log or "[]") + rollback_log, indent=2
	)
	changeset.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "rolled_back", "rollback_log": rollback_log}
