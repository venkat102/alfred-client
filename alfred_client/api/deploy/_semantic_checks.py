"""Per-doctype semantic checks for the dry-run pipeline.

The ``_meta_check_only`` path in ``_routing`` instantiates a doc via
``frappe.get_doc`` (no DB write) and then runs the per-doctype checker
from ``_DOCTYPE_SPECIFIC_CHECKS`` registered below. Each checker is a
pure function that raises ``ValueError`` with a descriptive message on
the first failure.

Adding a new checker:

  1. Define the function in this module (must take ``data: dict``).
  2. Register it in ``_DOCTYPE_SPECIFIC_CHECKS`` keyed by the target
     doctype name.
  3. Add a unit test in ``tests/test_deploy.py`` /
     ``tests/test_dry_run_safety.py``.

Test 7 in ``test_dry_run_safety.py`` pins the contract that
``_check_custom_field`` raises a clear "already exists" message when
the chosen fieldname collides with an existing standard or custom
field on the target DocType - that's the signal the dry-run severity
classifier uses to upgrade the issue to ``critical``.
"""

from __future__ import annotations

import frappe


def _check_custom_field(data):
	target = data.get("dt")
	if not target:
		raise ValueError("Custom Field requires 'dt' (target DocType)")
	if not frappe.db.exists("DocType", target):
		raise ValueError(f"Custom Field target DocType '{target}' does not exist")
	fieldname = data.get("fieldname")
	if not fieldname:
		raise ValueError("Custom Field requires 'fieldname'")
	if not data.get("fieldtype"):
		raise ValueError("Custom Field requires 'fieldtype'")
	# Conflict with an existing standard field
	meta = frappe.get_meta(target)
	if meta.get_field(fieldname):
		existing = "standard" if fieldname in {f.fieldname for f in meta.fields if not getattr(f, "is_custom_field", 0)} else "custom"
		raise ValueError(
			f"Field '{fieldname}' already exists on '{target}' (as {existing} field)"
		)


def _check_doctype(data):
	name = data.get("name")
	if not name:
		raise ValueError("DocType requires 'name'")
	if frappe.db.exists("DocType", name):
		raise ValueError(f"DocType '{name}' already exists")
	if not data.get("module"):
		raise ValueError("DocType requires 'module'")
	fields = data.get("fields") or []
	if not fields:
		raise ValueError("DocType must have at least one field")


def _check_workflow(data):
	target = data.get("document_type")
	if not target:
		raise ValueError("Workflow requires 'document_type'")
	if not frappe.db.exists("DocType", target):
		raise ValueError(f"Workflow document_type '{target}' does not exist")
	states = data.get("states") or []
	if not states:
		raise ValueError("Workflow must have at least one state")
	transitions = data.get("transitions") or []
	if not transitions:
		raise ValueError("Workflow must have at least one transition")
	# Every transition's state and next_state must be declared in states
	declared = {s.get("state") for s in states if isinstance(s, dict)}
	for idx, t in enumerate(transitions, 1):
		if not isinstance(t, dict):
			raise ValueError(f"Workflow transition {idx} must be an object")
		for key in ("state", "next_state"):
			val = t.get(key)
			if val and val not in declared:
				raise ValueError(
					f"Workflow transition {idx} {key} '{val}' is not declared in states"
				)
	# Submittable vs non-submittable consistency check
	try:
		meta = frappe.get_meta(target)
		if not meta.is_submittable:
			for s in states:
				if isinstance(s, dict) and (s.get("doc_status") or 0) != 0:
					raise ValueError(
						f"Workflow state '{s.get('state')}' has doc_status "
						f"{s.get('doc_status')} but '{target}' is not submittable - "
						"only doc_status 0 is allowed for non-submittable doctypes"
					)
	except ValueError:
		raise
	except Exception:
		pass


def _check_notification(data):
	target = data.get("document_type")
	if target and not frappe.db.exists("DocType", target):
		raise ValueError(f"Notification document_type '{target}' does not exist")


def _check_server_script(data):
	target = data.get("reference_doctype")
	if target and not frappe.db.exists("DocType", target):
		raise ValueError(f"Server Script reference_doctype '{target}' does not exist")


def _check_client_script(data):
	target = data.get("dt")
	if target and not frappe.db.exists("DocType", target):
		raise ValueError(f"Client Script dt '{target}' does not exist")


_DOCTYPE_SPECIFIC_CHECKS = {
	"Custom Field": _check_custom_field,
	"DocType": _check_doctype,
	"Workflow": _check_workflow,
	"Notification": _check_notification,
	"Server Script": _check_server_script,
	"Client Script": _check_client_script,
}
