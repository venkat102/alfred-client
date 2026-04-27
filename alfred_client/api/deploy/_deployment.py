"""Forward deploy path: ``apply_changeset`` orchestration + verification.

The user-facing entry point is ``apply_changeset`` (Frappe whitelist).
It runs every change under the conversation owner's identity (via
``_acting_as``), with permission re-verification per item, write-ahead
audit logging, and a single transaction boundary - on failure the whole
batch rolls back via ``frappe.db.rollback()`` and the changeset is
marked "Rolled Back".

After successful commit, ``verify_deployment`` does post-deploy sanity
checks (does the doc exist? are the fields present? are the
permissions wired? is the workflow active?) and reports any drift.

This module imports ``_write_audit_log`` from ``_rollback`` for the
write-ahead audit rows that fire on every forward operation. The edge
goes ``_deployment`` -> ``_rollback`` only; ``_rollback`` does not
import from ``_deployment``, so there is no cycle.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import frappe
from frappe import _

from alfred_client.api.deploy._rollback import _write_audit_log


# DocTypes whose successful insert/update should trigger a defensive meta
# cache clear on the affected target. Frappe's own on_update hooks already
# clear meta for these, so this is paranoid-correct - it costs O(few ms)
# and prevents a follow-up tool call within the same agent run from reading
# stale meta if any hook regresses.
_META_AFFECTING_DOCTYPES = {
	"Custom Field", "Property Setter", "DocType",
	"DocPerm", "Custom DocPerm", "Workflow",
}


# ── Safe context-switch helper ───────────────────────────────────
#
# frappe.set_user() is destructive: frappe/__init__.py:366 sets
# local.session.sid = username, local.session.data = _dict(), and resets a
# handful of caches. In an interactive HTTP request that means the caller's
# real session id (a UUID) gets overwritten with a username string, and the
# CSRF token inside session.data is wiped. When the response goes back to
# the browser the cookie sid still matches on the client but the server-side
# session record has been clobbered - the next AJAX call fails CSRF /
# authentication and the client is bounced to login.
#
# This context manager preserves the full triple (user, sid, data) across
# the set_user window so the browser session survives the deploy. We also
# clear the same caches set_user touches so permission checks re-run against
# the restored identity cleanly.
@contextmanager
def _acting_as(target_user: str):
	snapshot_user = frappe.session.user
	snapshot_sid = getattr(frappe.local.session, "sid", None)
	snapshot_data = getattr(frappe.local.session, "data", None)

	frappe.set_user(target_user)
	try:
		yield
	finally:
		frappe.local.session.user = snapshot_user
		if snapshot_sid is not None:
			frappe.local.session.sid = snapshot_sid
		if snapshot_data is not None:
			frappe.local.session.data = snapshot_data
		# The caches set_user() resets must be cleared here too so that any
		# code after the context sees role/perms keyed to snapshot_user, not
		# target_user's stale entries.
		frappe.local.cache = {}
		frappe.local.form_dict = frappe._dict()
		frappe.local.jenv = None
		frappe.local.role_permissions = {}
		frappe.local.new_doc_templates = {}
		frappe.local.user_perms = None


@frappe.whitelist()
def apply_changeset(changeset_name):
	"""Apply an approved changeset to the site.

	Caller must have write permission on the changeset (owner of the parent
	conversation, shared-with-write, or System Manager). Operations then run
	as the conversation owner (not the caller) so the owner's permissions
	are what's enforced on each create/update - this is a deliberate two-
	step authorization: the CALLER needs permission to trigger, and the
	OWNER's permissions apply to the actual writes.

	On any failure, automatically rolls back all previously applied changes.

	Args:
		changeset_name: Name of the Alfred Changeset document.

	Returns:
		Dict with execution results, verification, and rollback data.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission(
		"Alfred Changeset", ptype="write", doc=changeset_name, throw=True,
	)

	changeset = frappe.get_doc("Alfred Changeset", changeset_name)

	if changeset.status != "Approved":
		frappe.throw(_("Changeset must be approved before deployment. Current status: {0}").format(changeset.status))

	# Distributed lock: atomically set status to "Deploying" to prevent
	# concurrent deployment. The WHERE clause ensures only one process can
	# transition from Approved to Deploying.
	#
	# SUBTLE: just checking `status == "Deploying"` after reload is NOT enough
	# to confirm we won the lock - if process A flipped the status first and
	# then process B's UPDATE matches zero rows, both processes still see
	# status="Deploying" on reload. We need rowcount to know whether OUR
	# UPDATE is the one that changed the row.
	frappe.db.sql(
		"""UPDATE `tabAlfred Changeset` SET status='Deploying'
		   WHERE name=%s AND status='Approved'""",
		changeset_name,
	)
	won_lock = (
		getattr(frappe.db, "_cursor", None) is not None
		and frappe.db._cursor.rowcount == 1
	)
	frappe.db.commit()

	if not won_lock:
		changeset.reload()
		frappe.throw(
			_("Changeset cannot be deployed (status: {0}). Another process may "
			  "have already started this deployment.").format(changeset.status)
		)

	# Get the requesting user from the parent conversation
	conversation = frappe.get_doc("Alfred Conversation", changeset.conversation)
	requesting_user = conversation.user

	changes = json.loads(changeset.changes) if isinstance(changeset.changes, str) else changeset.changes
	if not changes:
		return {"status": "success", "message": "No changes to deploy", "steps": []}

	execution_log = []
	rollback_data = []
	failed = False
	error_msg = ""

	# Run every write under the requesting user's identity. The context
	# manager snapshots and restores session.sid / session.data so the
	# caller's browser session survives the context switch - without this,
	# frappe.set_user() wipes the CSRF token and bounces the user to login
	# on the next AJAX call.
	verification = None
	with _acting_as(requesting_user):
		try:
			for i, change in enumerate(changes):
				step = i + 1
				operation = change.get("op", change.get("operation", "create"))
				doctype = change.get("doctype", "")
				data = change.get("data", {})
				doc_name = data.get("name", "")

				# Server-side permission re-verification before EACH operation
				perm_action = "create" if operation == "create" else "write"
				if not frappe.has_permission(doctype, perm_action):
					raise frappe.PermissionError(
						_("User '{0}' does not have '{1}' permission on '{2}'").format(
							requesting_user, perm_action, doctype
						)
					)

				# Publish progress
				frappe.publish_realtime(
					"alfred_deploy_progress",
					{
						"changeset": changeset_name,
						"step": step,
						"total": len(changes),
						"operation": operation,
						"doctype": doctype,
						"name": doc_name,
						"status": "in_progress",
					},
					user=requesting_user,
				)

				# Write audit log BEFORE execution (write-ahead)
				_write_audit_log(
					changeset.conversation, doctype, doc_name, operation,
					before_state=_get_document_state(doctype, doc_name) if operation == "update" else None,
				)

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
				else:
					raise ValueError(f"Unknown operation: {operation}")

				# Belt-and-braces meta cache clear. Frappe's own on_update
				# hooks for Custom Field / Property Setter / DocPerm already
				# trigger this, but a redundant call costs O(few ms) and
				# prevents stale-meta misreads by a follow-up tool call (e.g.
				# get_doctype_context) within the same agent run.
				if doctype in _META_AFFECTING_DOCTYPES:
					target = data.get("dt") or data.get("doc_type") \
						or data.get("parent") or data.get("name")
					if target and isinstance(target, str):
						try:
							frappe.clear_cache(doctype=target)
						except Exception:
							pass  # never let a cache clear break a deploy

				execution_log.append({
					"step": step,
					"operation": operation,
					"doctype": doctype,
					"name": result.get("name", doc_name),
					"status": "success",
				})

				frappe.publish_realtime(
					"alfred_deploy_progress",
					{"changeset": changeset_name, "step": step, "total": len(changes), "status": "success", "name": doc_name},
					user=requesting_user,
				)

		except Exception as e:
			failed = True
			error_msg = str(e)
			execution_log.append({
				"step": len(execution_log) + 1,
				"operation": change.get("op", change.get("operation", "")),
				"doctype": change.get("doctype", ""),
				"name": change.get("data", {}).get("name", ""),
				"status": "failed",
				"error": error_msg,
			})

			# Database-level rollback - undoes ALL uncommitted operations in this transaction.
			# This is safer than manual rollback because it's atomic.
			frappe.db.rollback()

			frappe.publish_realtime(
				"alfred_deploy_failed",
				{"changeset": changeset_name, "step": len(execution_log), "error": error_msg, "rollback_initiated": True},
				user=requesting_user,
			)

		# Post-deployment: verification or commit (inside the acting-as
		# context so verification reads run under the requesting user).
		if failed:
			changeset.status = "Rolled Back"
			changeset.deployment_log = json.dumps(execution_log, indent=2)
		else:
			# Commit the entire deployment as a single transaction
			frappe.db.commit()
			# Verify deployment AFTER commit
			verification = verify_deployment(changes, changeset.conversation)
			changeset.status = "Deployed"
			changeset.deployment_log = json.dumps(execution_log, indent=2)

	# Context exited - session is restored to the caller. Save the status
	# update as the original (caller) user with ignore_permissions=True.
	changeset.rollback_data = json.dumps(rollback_data)
	changeset.save(ignore_permissions=True)
	frappe.db.commit()

	if not failed:
		frappe.publish_realtime(
			"alfred_deploy_complete",
			{"changeset": changeset_name, "status": "success", "steps": len(execution_log), "verification": verification},
			user=requesting_user,
		)

	return {
		"status": "success" if not failed else "failed",
		"error": error_msg if failed else None,
		"execution_log": execution_log,
		"rollback_data": rollback_data,
		"verification": verification,
	}


# ── Document Operations (ignore_permissions=False) ────────────────

def _create_document(doctype, data):
	"""Create a Frappe document. Runs with user's permissions.

	Does NOT commit - the caller manages the transaction boundary.
	"""
	doc_data = dict(data)
	doc_data["doctype"] = doctype

	doc = frappe.get_doc(doc_data)
	doc.insert(ignore_permissions=False)
	return {"name": doc.name, "created": True}


def _update_document(doctype, data):
	"""Update an existing document. Runs with user's permissions.

	Does NOT commit - the caller manages the transaction boundary.
	"""
	doc_name = data.get("name")
	if not doc_name:
		raise ValueError("Document name is required for update operation")

	doc = frappe.get_doc(doctype, doc_name)
	doc.update(data)
	doc.save(ignore_permissions=False)
	return {"name": doc.name, "updated": True}


def _get_document_state(doctype, name):
	"""Get the current state of a document for rollback/audit."""
	try:
		doc = frappe.get_doc(doctype, name)
		return doc.as_dict()
	except frappe.DoesNotExistError:
		return None


# ── Post-Deployment Verification ─────────────────────────────────

def verify_deployment(changes, conversation_name):
	"""Verify that all documents were created/modified as expected.

	Runs after successful deployment to catch silent failures.

	Returns:
		Dict with verification results.
	"""
	issues = []
	all_exist = True
	all_fields = True
	all_permissions = True

	for change in changes:
		operation = change.get("op", change.get("operation", "create"))
		doctype = change.get("doctype", "")
		data = change.get("data", {})
		doc_name = data.get("name", "")

		if not doc_name:
			continue

		# 1. Check document exists
		if not frappe.db.exists(doctype, doc_name):
			issues.append({
				"severity": "error",
				"document": f"{doctype}: {doc_name}",
				"issue": "Document does not exist after deployment",
			})
			all_exist = False
			continue

		# 2. For DocTypes: verify fields are present
		if doctype == "DocType" and operation == "create":
			expected_fields = data.get("fields", [])
			try:
				meta = frappe.get_meta(doc_name)
				actual_fieldnames = {f.fieldname for f in meta.fields}

				for field in expected_fields:
					fn = field.get("fieldname", "")
					if fn and fn not in actual_fieldnames:
						issues.append({
							"severity": "warning",
							"document": f"DocType: {doc_name}",
							"issue": f"Field '{fn}' not found after deployment",
						})
						all_fields = False
			except Exception as e:
				issues.append({
					"severity": "warning",
					"document": f"DocType: {doc_name}",
					"issue": f"Could not verify fields: {e}",
				})

		# 3. For DocTypes: verify permissions
		if doctype == "DocType" and operation == "create":
			expected_permissions = data.get("permissions", [])
			try:
				meta = frappe.get_meta(doc_name)
				actual_roles = {p.role for p in meta.permissions}

				for perm in expected_permissions:
					role = perm.get("role", "")
					if role and role not in actual_roles:
						issues.append({
							"severity": "warning",
							"document": f"DocType: {doc_name}",
							"issue": f"Permission for role '{role}' not found",
						})
						all_permissions = False
			except Exception:
				pass

		# 4. For Server Scripts: verify content
		if doctype == "Server Script" and operation == "create":
			try:
				doc = frappe.get_doc("Server Script", doc_name)
				expected_script = data.get("script", "")
				if expected_script:
					actual = (doc.script or "").strip()
					expected = expected_script.strip()
					# Normalize whitespace for comparison
					if actual.replace("\r\n", "\n") != expected.replace("\r\n", "\n"):
						issues.append({
							"severity": "warning",
							"document": f"Server Script: {doc_name}",
							"issue": "Script content differs from expected (may be whitespace difference)",
						})
			except Exception:
				pass

		# 5. For Workflows: verify active state
		if doctype == "Workflow" and operation == "create":
			try:
				doc = frappe.get_doc("Workflow", doc_name)
				if not doc.is_active:
					issues.append({
						"severity": "warning",
						"document": f"Workflow: {doc_name}",
						"issue": "Workflow is not active after deployment",
					})
			except Exception:
				pass

	return {
		"all_documents_exist": all_exist,
		"all_fields_present": all_fields,
		"all_permissions_correct": all_permissions,
		"issues": issues,
		"verified_count": len(changes),
	}
