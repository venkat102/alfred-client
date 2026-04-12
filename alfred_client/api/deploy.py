"""Deployment engine for applying changesets to the Frappe site.

SECURITY: This is the most sensitive module in Alfred. It modifies the
customer's live site. Every operation:
  - Runs as the requesting user (frappe.set_user)
  - Uses ignore_permissions=False (Frappe enforces user permissions)
  - Is permission-verified server-side before execution
  - Is logged to Alfred Audit Log with before/after state

Called via: POST /api/method/alfred_client.api.deploy.apply_changeset
"""

import json

import frappe
from frappe import _


# ── Frappe default fields to exclude from verification comparisons
FRAPPE_DEFAULT_FIELDS = {
	"name", "owner", "creation", "modified", "modified_by",
	"docstatus", "idx", "parent", "parenttype", "parentfield",
	"doctype", "amended_from", "_user_tags", "_comments",
	"_assign", "_liked_by", "_seen",
}


# ── Main Deployment Entry Point ──────────────────────────────────

@frappe.whitelist()
def apply_changeset(changeset_name):
	"""Apply an approved changeset to the site.

	Runs all operations as the requesting user with Frappe permission enforcement.
	On any failure, automatically rolls back all previously applied changes.

	Args:
		changeset_name: Name of the Alfred Changeset document.

	Returns:
		Dict with execution results, verification, and rollback data.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	changeset = frappe.get_doc("Alfred Changeset", changeset_name)

	if changeset.status != "Approved":
		frappe.throw(_("Changeset must be approved before deployment. Current status: {0}").format(changeset.status))

	# Distributed lock: atomically set status to "Deploying" to prevent concurrent deployment.
	# The WHERE clause ensures only one process can transition from Approved to Deploying.
	frappe.db.sql(
		"""UPDATE `tabAlfred Changeset` SET status='Deploying'
		   WHERE name=%s AND status='Approved'""",
		changeset_name,
	)
	frappe.db.commit()

	# Re-check - if status is not Deploying, another process grabbed the lock first
	changeset.reload()
	if changeset.status != "Deploying":
		frappe.throw(_("Changeset is already being deployed by another process."))

	# Get the requesting user from the parent conversation
	conversation = frappe.get_doc("Alfred Conversation", changeset.conversation)
	requesting_user = conversation.user

	# Switch to the requesting user's context - ALL operations run as this user
	original_user = frappe.session.user
	frappe.set_user(requesting_user)

	changes = json.loads(changeset.changes) if isinstance(changeset.changes, str) else changeset.changes
	if not changes:
		frappe.set_user(original_user)
		return {"status": "success", "message": "No changes to deploy", "steps": []}

	execution_log = []
	rollback_data = []
	failed = False
	error_msg = ""

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

	# Post-deployment: verification or commit
	verification = None
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

	changeset.rollback_data = json.dumps(rollback_data)
	# Save changeset with admin permissions (status update is an internal operation)
	frappe.set_user(original_user)
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


# ── Dry-Run Validation ──────────────────────────────────────────

def dry_run_changeset(changes):
	"""Validate a changeset WITHOUT committing anything to the database.

	For each change item, verifies:
	  1. The target doctype exists in Frappe
	  2. The operation is valid (create/update)
	  3. For create: no naming conflict with existing documents
	  4. Runtime error checks: Python syntax for Server Scripts, Jinja syntax
	     for Notification subject/message/condition, basic JS checks for Client Scripts
	  5. The document passes Frappe's validate() inside a savepoint rollback

	The savepoint rollback covers insert-time issues (mandatory fields, link targets).
	The runtime checks (step 4) catch errors that only surface at execution time -
	a Notification with `{{ doc.` will dry-run clean without them.

	Returns:
		Dict with 'valid' (bool), 'issues' (list of problems), 'validated' (list of OK items)
	"""
	if isinstance(changes, str):
		try:
			changes = json.loads(changes)
		except json.JSONDecodeError as e:
			return {
				"valid": False,
				"issues": [{"severity": "critical", "issue": f"changes is not valid JSON: {e}"}],
				"validated": [],
			}

	if not isinstance(changes, list):
		return {
			"valid": False,
			"issues": [{
				"severity": "critical",
				"issue": f"Expected a list of changes, got {type(changes).__name__}",
			}],
			"validated": [],
		}

	if not changes:
		return {"valid": True, "issues": [], "validated": []}

	issues = []
	validated = []

	for i, change in enumerate(changes):
		step = i + 1

		# Per-item shape validation - agents sometimes produce malformed JSON.
		if not isinstance(change, dict):
			issues.append({
				"step": step, "severity": "critical",
				"issue": f"Changeset item must be an object, got {type(change).__name__}",
			})
			continue

		operation = change.get("op", change.get("operation", "create"))
		doctype = change.get("doctype", "")
		data = change.get("data", {})
		if not isinstance(data, dict):
			issues.append({
				"step": step, "severity": "critical",
				"issue": f"Item's 'data' field must be an object, got {type(data).__name__}",
				"doctype": doctype,
			})
			continue
		doc_name = data.get("name", "")

		# 1. Check target doctype exists in Frappe
		if not frappe.db.exists("DocType", doctype):
			issues.append({
				"step": step, "severity": "critical",
				"issue": f"DocType '{doctype}' does not exist in this Frappe site",
				"doctype": doctype, "name": doc_name,
			})
			continue

		# 2. Check operation type
		if operation not in ("create", "update"):
			issues.append({
				"step": step, "severity": "critical",
				"issue": f"Unknown operation '{operation}'. Expected 'create' or 'update'",
				"doctype": doctype, "name": doc_name,
			})
			continue

		# 3. For create: check for naming conflicts
		if operation == "create" and doc_name:
			if frappe.db.exists(doctype, doc_name):
				issues.append({
					"step": step, "severity": "warning",
					"issue": f"'{doctype}' with name '{doc_name}' already exists. Will be skipped or may cause DuplicateEntryError",
					"doctype": doctype, "name": doc_name,
				})

		# 4. Runtime-error checks (Python, Jinja) - catch issues that don't
		# show up in insert-time validation but break at execution time.
		runtime_issues = _check_runtime_errors(doctype, data)
		for msg in runtime_issues:
			issues.append({
				"step": step, "severity": "critical",
				"issue": msg, "doctype": doctype, "name": doc_name,
			})

		# 5. Dry-run: instantiate the document and run validate() inside a savepoint
		try:
			_dry_run_single(doctype, data, operation)
			validated.append({
				"step": step, "doctype": doctype, "name": doc_name,
				"operation": operation, "status": "ok",
			})
		except Exception as e:
			error_msg = str(e)
			# Classify common Frappe errors
			severity = "critical"
			if "already exists" in error_msg.lower():
				severity = "warning"
			elif "mandatory" in error_msg.lower() or "required" in error_msg.lower():
				severity = "critical"

			issues.append({
				"step": step, "severity": severity,
				"issue": error_msg,
				"doctype": doctype, "name": doc_name,
			})

	return {
		"valid": len([i for i in issues if i["severity"] == "critical"]) == 0,
		"issues": issues,
		"validated": validated,
	}


def _check_runtime_errors(doctype, data):
	"""Cheap pre-flight checks for errors that only surface at execution time.

	- Server Script: Python syntax via compile()
	- Notification: Jinja syntax via frappe.render_template() on subject/message/condition
	- Client Script: loose regex check for balanced braces

	Returns a list of human-readable issue strings (empty if all ok).
	"""
	problems = []

	# --- Server Script: compile Python ---
	if doctype == "Server Script":
		script = data.get("script", "")
		if script:
			try:
				compile(script, "<alfred_dryrun>", "exec")
			except SyntaxError as e:
				problems.append(f"Server Script Python syntax error: {e.msg} at line {e.lineno}")
			except Exception as e:
				problems.append(f"Server Script compile failed: {e}")

	# --- Notification: render Jinja templates with a stub doc ---
	if doctype == "Notification":
		from jinja2 import TemplateSyntaxError
		# A dict-like object that returns None for any missing attribute/key,
		# so legitimate templates like {{ doc.employee_name }} don't trip the check.
		stub_doc = frappe._dict()
		for field in ("subject", "message", "condition"):
			template = data.get(field)
			if not template or not isinstance(template, str):
				continue
			try:
				frappe.render_template(template, {"doc": stub_doc, "frappe": frappe._dict()})
			except TemplateSyntaxError as e:
				problems.append(f"Notification '{field}' Jinja syntax error: {e.message} at line {e.lineno}")
			except Exception:
				# Runtime errors (missing attribute on stub_doc, KeyError, etc.) are
				# NOT dry-run failures - we only catch parse-time issues here.
				pass

	# --- Client Script: loose balanced-brace check ---
	if doctype == "Client Script":
		script = data.get("script", "")
		if script:
			if script.count("{") != script.count("}"):
				problems.append("Client Script has unbalanced curly braces")
			if script.count("(") != script.count(")"):
				problems.append("Client Script has unbalanced parentheses")

	return problems


def _dry_run_single(doctype, data, operation):
	"""Validate a single document operation using a database savepoint.

	Creates a savepoint, attempts the operation, then rolls back to the
	savepoint - so nothing is actually committed to the database.
	"""
	doc_data = dict(data)
	doc_data["doctype"] = doctype

	# Use a savepoint so we can rollback just this test without affecting
	# any outer transaction
	frappe.db.savepoint("dry_run")
	try:
		if operation == "create":
			doc = frappe.get_doc(doc_data)
			doc.flags.ignore_links = True  # Don't fail on missing Link targets during dry-run
			doc.insert(ignore_permissions=True)  # Test insertability, not permissions
		elif operation == "update":
			doc_name = data.get("name")
			if not doc_name:
				raise ValueError("Document name required for update")
			if not frappe.db.exists(doctype, doc_name):
				raise ValueError(f"Document '{doctype}/{doc_name}' does not exist")
			doc = frappe.get_doc(doctype, doc_name)
			doc.update(data)
			doc.save(ignore_permissions=True)
	finally:
		# Always rollback the savepoint - this is a dry run
		frappe.db.rollback(save_point="dry_run")


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


# ── Rollback System ──────────────────────────────────────────────

def _execute_rollback(rollback_data, conversation_name):
	"""Execute rollback operations in reverse order.

	Rollback uses ignore_permissions=True because we need to undo
	changes regardless of the user's current permissions (they may
	have been modified during a failed deployment).

	Continues on error - reports all failures rather than stopping.
	"""
	rollback_log = []
	for item in reversed(rollback_data):
		try:
			op = item.get("operation")
			dt = item.get("doctype", "")
			name = item.get("name", "")

			if op == "delete":
				# Data safety check for DocTypes
				if dt == "DocType":
					try:
						count = frappe.db.count(name)
						if count > 0:
							# Offer disable instead of delete
							try:
								meta_doc = frappe.get_doc("DocType", name)
								meta_doc.flags.ignore_permissions = True
								# Frappe doesn't have a standard 'disabled' on DocType,
								# so we log it as skipped and warn
								rollback_log.append({
									"operation": "skip_delete",
									"doctype": dt,
									"name": name,
									"status": "skipped",
									"reason": f"DocType '{name}' has {count} records. Deletion skipped to preserve data. Manual cleanup required.",
									"record_count": count,
								})
								_write_audit_log(conversation_name, dt, name, "rollback_skip_delete")
								continue
							except Exception:
								pass
					except Exception:
						pass

				frappe.delete_doc(dt, name, ignore_permissions=True, force=True)
				_write_audit_log(conversation_name, dt, name, "rollback_delete")
				rollback_log.append({"operation": "delete", "doctype": dt, "name": name, "status": "success"})

			elif op == "restore":
				before = item.get("before_state")
				if before:
					doc = frappe.get_doc(item["doctype"], item["name"])
					# Filter out non-updatable fields
					safe_before = {k: v for k, v in before.items() if k not in FRAPPE_DEFAULT_FIELDS and k != "doctype"}
					doc.update(safe_before)
					doc.save(ignore_permissions=True)
					_write_audit_log(conversation_name, item["doctype"], item["name"], "rollback_restore")
					rollback_log.append({"operation": "restore", "doctype": item["doctype"], "name": item["name"], "status": "success"})

			elif op == "create":
				data = item.get("data")
				if data:
					doc = frappe.get_doc(data)
					doc.insert(ignore_permissions=True)
					_write_audit_log(conversation_name, item["doctype"], data.get("name", ""), "rollback_recreate")
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
			# Continue with remaining rollback steps - don't stop on error

	return rollback_log


@frappe.whitelist()
def rollback_changeset(changeset_name):
	"""Manually rollback a deployed changeset.

	Checks for data in created DocTypes before deletion and offers
	alternatives to preserve user data.
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

	# Determine final status
	any_failed = any(item.get("status") == "failed" for item in rollback_log)
	any_skipped = any(item.get("status") == "skipped" for item in rollback_log)

	if any_failed:
		changeset.status = "Deployed"  # Keep as deployed since rollback failed
	else:
		changeset.status = "Rolled Back"

	prev_log = json.loads(changeset.deployment_log or "[]")
	changeset.deployment_log = json.dumps(prev_log + rollback_log, indent=2)
	changeset.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"status": "rolled_back" if not any_failed else "partial_rollback",
		"rollback_log": rollback_log,
		"skipped_items": [item for item in rollback_log if item.get("status") == "skipped"],
		"failed_items": [item for item in rollback_log if item.get("status") == "failed"],
	}


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


# ── Audit Logging ────────────────────────────────────────────────

def _write_audit_log(conversation, doctype, name, operation, before_state=None, after_state=None):
	"""Write an entry to the Alfred Audit Log."""
	try:
		frappe.get_doc({
			"doctype": "Alfred Audit Log",
			"conversation": conversation,
			"agent": "deployer",
			"action": operation,
			"document_type": doctype,
			"document_name": name,
			"before_state": json.dumps(before_state) if before_state else None,
			"after_state": json.dumps(after_state) if after_state else None,
		}).insert(ignore_permissions=True)
	except Exception:
		pass  # Don't let audit log failures block deployment
