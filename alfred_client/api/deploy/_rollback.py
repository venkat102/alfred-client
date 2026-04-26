"""Rollback execution + audit logging.

Two surfaces:

  - ``rollback_changeset`` (``@frappe.whitelist``): the user-facing
    "undo this deploy" button on a Deployed changeset. Walks the
    rollback_data list saved by apply_changeset and executes the
    inverse operations.
  - ``_execute_rollback``: the inner helper used by both
    ``rollback_changeset`` and the auto-rollback branch inside
    ``apply_changeset`` on deploy failure.

``_write_audit_log`` lives here too because both rollback paths emit
audit rows ("rollback_delete" / "rollback_restore" actions). The
``apply_changeset`` path in ``_deployment.py`` imports
``_write_audit_log`` from here to log forward operations as well.
``_deployment`` -> ``_rollback`` is a single-direction edge - no
import cycle.
"""

from __future__ import annotations

import json

import frappe
from frappe import _

from alfred_client.api.deploy._constants import FRAPPE_DEFAULT_FIELDS


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

			# Note: there is no `elif op == "create":` branch. apply_changeset
			# only ever adds "delete" (for forward "create") and "restore"
			# (for forward "update") to rollback_data - those are the only
			# two ops that need inverses. A "create" rollback would require
			# the forward path to have a "delete" op, which doesn't exist.

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

	SECURITY: rollback uses ignore_permissions=True (via _execute_rollback
	which calls frappe.delete_doc with force=True). That is the correct
	runtime behaviour for an inverse operation, but it means we MUST gate
	the trigger itself on write permission. Without that gate, any user
	with the Alfred role could undo another user's deploy and trigger
	privileged deletes on documents they don't own.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission(
		"Alfred Changeset", ptype="write", doc=changeset_name, throw=True,
	)

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
