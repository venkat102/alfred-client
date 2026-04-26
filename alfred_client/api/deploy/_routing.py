"""Dry-run routing: pick savepoint vs meta-check based on doctype.

Three layers:

  - ``dry_run_changeset(changes)``: orchestrator. Walks the changeset
    list, applies per-item shape validation + naming-conflict pre-check
    + runtime-error pre-flight + the savepoint/meta dispatch. Used by
    the UI (alfred_chat.py:453), the MCP tool layer (mcp/tools.py:608),
    and the existing test suite.
  - ``_dry_run_single(doctype, data, operation)``: dispatcher. Routes
    to ``_meta_check_only`` for DDL-triggering doctypes (where calling
    .insert() inside a savepoint would still implicitly commit because
    DDL forces it) and to ``_savepoint_dry_run`` for DML-only doctypes.
  - ``_meta_check_only`` / ``_savepoint_dry_run``: the two backends.

Test fixture note: ``test_dry_run_safety.Test 3`` monkeypatches
``_meta_check_only`` and ``_savepoint_dry_run`` to verify the dispatch
logic. Post-split, the test must monkeypatch them on THIS module
(``alfred_client.api.deploy._routing``), not on the package root,
because Python resolves bare-name calls against the module's own
globals dict - patching the package re-export does not redirect calls
made from inside this module.
"""

from __future__ import annotations

import json

import frappe

from alfred_client.api.deploy._constants import (
	_DDL_TRIGGERING_DOCTYPES,
	_SAVEPOINT_SAFE_DOCTYPES,
)
from alfred_client.api.deploy._runtime_validation import _check_runtime_errors
from alfred_client.api.deploy._semantic_checks import _DOCTYPE_SPECIFIC_CHECKS


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
			# Every dry-run failure is a real failure: meta-check raises only
			# on doctype-specific conflicts the agent must have missed (e.g.
			# Custom Field where the fieldname already exists on the target
			# DocType), and savepoint-validate raises only on errors the live
			# DB itself would throw at deploy time. There is no "soft" case
			# here - if dry-run failed, real deploy will fail in the exact
			# same place. Classifying these as warning was hiding bugs:
			# valid stayed True, the UI showed "Validated - ready to deploy",
			# the user approved, and the real deploy failed with the same
			# error. Pinned by test_dry_run_safety::Test 7.
			issues.append({
				"step": step, "severity": "critical",
				"issue": error_msg,
				"doctype": doctype, "name": doc_name,
			})

	return {
		"valid": len([i for i in issues if i["severity"] == "critical"]) == 0,
		"issues": issues,
		"validated": validated,
	}


def _dry_run_single(doctype, data, operation):
	"""Validate a single document operation without risking a commit.

	DDL-triggering doctypes go through `_meta_check_only`, which never calls
	.insert() and so can never cause an implicit MariaDB commit. Known-safe
	doctypes (where .insert() writes one row with no schema side effects)
	go through `_savepoint_dry_run` so we still catch controller validators
	and uniqueness constraints. Unknown doctypes fall through to
	`_meta_check_only` because the safe default is "don't touch the DB".
	"""
	if doctype in _DDL_TRIGGERING_DOCTYPES:
		_meta_check_only(doctype, data, operation)
		return
	if doctype in _SAVEPOINT_SAFE_DOCTYPES:
		_savepoint_dry_run(doctype, data, operation)
		return
	# Unknown doctype: be conservative. A new doctype we've never classified
	# might trigger schema changes we don't know about.
	_meta_check_only(doctype, data, operation)


def _savepoint_dry_run(doctype, data, operation):
	"""Savepoint-rollback path for known-safe doctypes only.

	This path calls .insert() / .save() so it catches controller-level errors
	the meta-only path would miss. Must NEVER be used for a doctype whose
	controller runs DDL via side effects - see `_DDL_TRIGGERING_DOCTYPES`.
	"""
	doc_data = dict(data)
	doc_data["doctype"] = doctype

	frappe.db.savepoint("dry_run")
	try:
		if operation == "create":
			doc = frappe.get_doc(doc_data)
			doc.flags.ignore_links = True
			doc.insert(ignore_permissions=True)
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
		frappe.db.rollback(save_point="dry_run")


def _meta_check_only(doctype, data, operation):
	"""Validate a document at the meta level only - never touches the DB.

	Checks:
	  1. `frappe.get_doc()` succeeds (catches unknown field names, bad
	     child-table shapes, unparseable values).
	  2. Mandatory fields are populated.
	  3. Link field targets exist on the live site.
	  4. Doctype-specific semantic checks (see `_DOCTYPE_SPECIFIC_CHECKS`).

	Raises with a descriptive message on the first failure. Intentionally
	does NOT call `.insert()`, `.save()`, or `.validate()` - any of those
	can trigger hooks that commit inline.
	"""
	doc_data = dict(data)
	doc_data["doctype"] = doctype

	if operation == "update":
		doc_name = data.get("name")
		if not doc_name:
			raise ValueError("Document name required for update")
		if not frappe.db.exists(doctype, doc_name):
			raise ValueError(f"Document '{doctype}/{doc_name}' does not exist")

	# 1. Instantiate. frappe.get_doc() builds the controller object and
	# coerces child-table rows but does not write anything.
	try:
		frappe.get_doc(doc_data)
	except Exception as e:
		raise ValueError(f"Document construction failed: {e}") from e

	# 2. Doctype-specific semantic checks (must run BEFORE the generic
	# mandatory-field walk so the caller sees the most actionable error).
	checker = _DOCTYPE_SPECIFIC_CHECKS.get(doctype)
	if checker:
		checker(doc_data)

	# 3. Generic mandatory-field check via the target doctype's meta.
	# DocType and Custom Field rows define other doctypes, so their own
	# "mandatory" fields come from frappe.get_meta(doctype). For most
	# types that's the right thing.
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		# If the meta itself can't be fetched, we've already failed the
		# "target doctype exists" check upstream - skip silently here.
		return

	missing = []
	for field in meta.fields:
		if not getattr(field, "reqd", 0):
			continue
		fieldname = field.fieldname
		value = doc_data.get(fieldname)
		if value in (None, "", []):
			missing.append(fieldname)
	if missing:
		raise ValueError(
			f"Missing mandatory field(s): {', '.join(missing)}"
		)

	# 4. Link field targets must exist on the live site.
	for field in meta.fields:
		if field.fieldtype != "Link":
			continue
		link_value = doc_data.get(field.fieldname)
		if not link_value:
			continue
		link_doctype = field.options
		if not link_doctype:
			continue
		if not frappe.db.exists(link_doctype, link_value):
			raise ValueError(
				f"Link field '{field.fieldname}' references "
				f"{link_doctype} '{link_value}' which does not exist"
			)
