"""Dry-run safety: DDL routing must never call .insert() on DDL-triggering
doctypes (DocType, Custom Field, Property Setter, Workflow, ...).

MariaDB implicitly commits all pending DML before any DDL statement. That
silently breaks a savepoint-rollback - the "test insert" lands in the DB
for real and the user sees it as if they'd approved a deploy.

These tests pin the routing so a future refactor can't silently move a
DDL-triggering type onto the savepoint path.

Run with: bench --site dev.alfred execute alfred_client.test_dry_run_safety.run_tests
"""

from __future__ import annotations

import frappe


def run_tests():
	print("\n=== Alfred Dry-Run DDL Safety Tests ===\n")

	from alfred_client.api import deploy as _deploy
	from alfred_client.api.deploy import (
		_DDL_TRIGGERING_DOCTYPES,
		_SAVEPOINT_SAFE_DOCTYPES,
		_dry_run_single,
	)

	# ── 1. The two sets must be disjoint. A doctype can't be in both or ──
	#     routing becomes ambiguous.
	print("Test 1: DDL and savepoint-safe sets are disjoint...")
	overlap = _DDL_TRIGGERING_DOCTYPES & _SAVEPOINT_SAFE_DOCTYPES
	assert not overlap, f"Overlap between DDL and savepoint sets: {overlap}"
	print("  PASSED\n")

	# ── 2. Critical DDL types must all be in _DDL_TRIGGERING_DOCTYPES ────
	print("Test 2: All known DDL-triggering doctypes are classified...")
	must_be_ddl = {
		"DocType", "Custom Field", "Property Setter",
		"Workflow", "Workflow State",
	}
	for dt in must_be_ddl:
		assert dt in _DDL_TRIGGERING_DOCTYPES, (
			f"{dt} MUST be in _DDL_TRIGGERING_DOCTYPES - its insert() "
			f"triggers DDL that breaks savepoint rollback"
		)
	print("  PASSED\n")

	# ── 3. DDL route: _dry_run_single for DocType must call _meta_check_only,
	#     NOT _savepoint_dry_run.
	print("Test 3: DocType routes to meta-check only (never calls .insert)...")
	calls = {"meta": 0, "savepoint": 0}
	orig_meta = _deploy._meta_check_only
	orig_sp = _deploy._savepoint_dry_run

	def fake_meta(dt, data, op):
		calls["meta"] += 1

	def fake_sp(dt, data, op):
		calls["savepoint"] += 1

	_deploy._meta_check_only = fake_meta
	_deploy._savepoint_dry_run = fake_sp
	try:
		_dry_run_single("DocType", {"name": "Test DT", "module": "Core"}, "create")
		assert calls["meta"] == 1, "DocType must route to _meta_check_only"
		assert calls["savepoint"] == 0, (
			"DocType must NEVER route to _savepoint_dry_run - its insert() "
			"triggers DDL which kills savepoints"
		)

		# Reset and test Custom Field
		calls["meta"] = calls["savepoint"] = 0
		_dry_run_single("Custom Field", {"dt": "User", "fieldname": "x", "fieldtype": "Data"}, "create")
		assert calls["meta"] == 1, "Custom Field must route to _meta_check_only"
		assert calls["savepoint"] == 0

		# Reset and test Server Script (savepoint-safe)
		calls["meta"] = calls["savepoint"] = 0
		_dry_run_single(
			"Server Script",
			{"name": "X", "script_type": "API", "api_method": "x", "script": "pass"},
			"create",
		)
		assert calls["savepoint"] == 1, "Server Script must route to _savepoint_dry_run"
		assert calls["meta"] == 0

		# Reset and test UNKNOWN doctype (must be conservative - meta-only)
		calls["meta"] = calls["savepoint"] = 0
		_dry_run_single(
			"Some Made Up DocType",
			{"name": "anything"},
			"create",
		)
		assert calls["meta"] == 1, (
			"Unknown doctype must fall back to meta-only - safer default"
		)
		assert calls["savepoint"] == 0
		print("  PASSED\n")
	finally:
		_deploy._meta_check_only = orig_meta
		_deploy._savepoint_dry_run = orig_sp

	# ── 4. End-to-end: dry-running a DocType creation must not leak the row.
	#     (Even if we routed to savepoint, DDL would commit it.)
	print("Test 4: Dry-running a DocType does not leak the row...")
	from alfred_client.api.deploy import dry_run_changeset

	probe_dt_name = "Alfred DRY RUN PROBE DT"
	# Ensure clean slate
	if frappe.db.exists("DocType", probe_dt_name):
		frappe.delete_doc("DocType", probe_dt_name, force=True, ignore_permissions=True)
		frappe.db.commit()

	result = dry_run_changeset([{
		"op": "create",
		"doctype": "DocType",
		"data": {
			"name": probe_dt_name,
			"module": "Core",
			"custom": 1,
			"fields": [
				{"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1},
			],
			"permissions": [{"role": "System Manager", "read": 1}],
		},
	}])
	# The probe DocType must NOT exist after dry-run (DDL bypass works).
	assert not frappe.db.exists("DocType", probe_dt_name), (
		"Dry-run of DocType leaked a row into the database - "
		"DDL-triggering type must never call .insert()"
	)
	print(f"  Dry-run result valid={result['valid']}, issues={len(result['issues'])}")
	print("  PASSED\n")

	# ── 5. Savepoint path: rolling back leaves no trace.
	print("Test 5: Savepoint-safe dry-run cleanly rolls back Server Script row...")
	probe_script = "Alfred DRY RUN PROBE SCRIPT"
	if frappe.db.exists("Server Script", probe_script):
		frappe.delete_doc("Server Script", probe_script, force=True, ignore_permissions=True)
		frappe.db.commit()

	result = dry_run_changeset([{
		"op": "create",
		"doctype": "Server Script",
		"data": {
			"name": probe_script,
			"script_type": "API",
			"api_method": "alfred_probe",
			"script": "pass",
			"disabled": 1,
		},
	}])
	assert not frappe.db.exists("Server Script", probe_script), (
		"Dry-run of Server Script leaked row - savepoint rollback failed"
	)
	print(f"  Dry-run result valid={result['valid']}, issues={len(result['issues'])}")
	print("  PASSED\n")

	# ── 6. Exception inside savepoint dry-run must still release it.
	#     A dangling savepoint blocks subsequent queries.
	print("Test 6: Savepoint is released after an exception inside dry-run...")
	# Trigger a construction failure. Server Script without required fields.
	result = dry_run_changeset([{
		"op": "create",
		"doctype": "Server Script",
		"data": {
			"name": "Alfred DRY RUN BROKEN",
			# Missing script_type, script, etc.
		},
	}])
	# Expect invalid result but NO exception at test level
	assert result["valid"] is False
	# Verify we can still execute subsequent queries - savepoint was released.
	next_count = frappe.db.count("DocType")
	assert next_count > 0, "DB unresponsive - savepoint may have been left open"
	print(f"  Follow-up query succeeded (DocType count={next_count})")
	print("  PASSED\n")

	# ── 7. Field-already-exists must be CRITICAL, not warning. ─────────
	#     User-reported bug: `priority` is a standard field on `ToDo`. The
	#     agent proposed creating a Custom Field for it; dry-run found the
	#     conflict but classified it as a warning, valid stayed True, the
	#     UI showed "Validated - ready to deploy", and the real deploy
	#     failed. Lock the contract: any "already exists" error from
	#     dry-run is critical, valid is False, and the warning is visible
	#     in the UI before approval.
	print("Test 7: Custom Field on existing standard field is CRITICAL...")
	from alfred_client.api.deploy import dry_run_changeset

	# Confirm `priority` is actually a standard field on ToDo - if Frappe
	# ever drops it from core, this test should switch to a different
	# standard field rather than silently pass.
	todo_meta = frappe.get_meta("ToDo")
	standard_field_on_todo = todo_meta.get_field("priority")
	assert standard_field_on_todo, (
		"Precondition failed: 'priority' is no longer a standard field on "
		"ToDo - update this test to use a different standard field."
	)

	result = dry_run_changeset([{
		"op": "create",
		"doctype": "Custom Field",
		"data": {
			"dt": "ToDo",
			"fieldname": "priority",
			"label": "Priority",
			"fieldtype": "Select",
			"options": "Low\nMedium\nHigh",
		},
	}])
	assert result["valid"] is False, (
		f"Expected valid=False for Custom Field colliding with standard "
		f"field, got valid={result['valid']} with issues={result['issues']}"
	)
	criticals = [i for i in result["issues"] if i.get("severity") == "critical"]
	assert criticals, (
		f"Expected at least one critical-severity issue, got "
		f"{result['issues']}"
	)
	matching = [i for i in criticals if "already exists" in (i.get("issue") or "").lower()]
	assert matching, (
		f"Expected a critical-severity issue mentioning 'already exists', "
		f"got criticals={criticals}"
	)
	print(f"  Caught {len(criticals)} critical issue(s); valid=False")
	print("  PASSED\n")

	print("=== All Dry-Run DDL Safety Tests PASSED ===\n")
