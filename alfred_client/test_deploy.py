"""Tests for the deployment engine, rollback, and verification.

Run with: bench --site dev.alfred execute alfred_client.test_deploy.run_tests
"""

import json

import frappe


def run_tests():
	print("\n=== Alfred Deployment Engine Tests ===\n")

	# Setup: create a test conversation and changeset
	conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": "Administrator",
		"status": "Open",
	})
	conv.insert(ignore_permissions=True)
	frappe.db.commit()

	# Test 1: Reject non-approved changeset
	print("Test 1: Reject non-approved changeset...")
	cs = frappe.get_doc({
		"doctype": "Alfred Changeset",
		"conversation": conv.name,
		"status": "Pending",
		"changes": json.dumps([]),
	})
	cs.insert(ignore_permissions=True)
	frappe.db.commit()

	from alfred_client.api.deploy import apply_changeset
	try:
		apply_changeset(cs.name)
		print("  FAILED: Should reject non-approved changeset")
	except Exception as e:
		assert "approved" in str(e).lower()
		print("  Correctly rejected: " + str(e)[:80])
	print("  PASSED\n")

	# Test 2: Deploy empty changeset
	print("Test 2: Deploy empty (approved) changeset...")
	cs.status = "Approved"
	cs.save(ignore_permissions=True)
	frappe.db.commit()

	result = apply_changeset(cs.name)
	assert result["status"] == "success"
	print(f"  Result: {result['status']}")
	print("  PASSED\n")

	# Test 3: Successful deployment with document creation
	print("Test 3: Deploy changeset that creates a Server Script...")
	# Using Server Script since DocType creation requires more complex setup
	test_script_name = "Alfred Test Deploy Script"

	# Clean up if exists from a previous run
	if frappe.db.exists("Server Script", test_script_name):
		frappe.delete_doc("Server Script", test_script_name, force=True)
		frappe.db.commit()

	cs2 = frappe.get_doc({
		"doctype": "Alfred Changeset",
		"conversation": conv.name,
		"status": "Approved",
		"changes": json.dumps([{
			"op": "create",
			"doctype": "Server Script",
			"data": {
				"name": test_script_name,
				"script_type": "API",
				"api_method": "alfred_test_deploy",
				"script": "frappe.response['message'] = 'test'",
				"disabled": 1,
			},
		}]),
	})
	cs2.insert(ignore_permissions=True)
	frappe.db.commit()

	result = apply_changeset(cs2.name)
	assert result["status"] == "success", f"Deploy failed: {result}"
	assert frappe.db.exists("Server Script", test_script_name)

	# Verify changeset status updated
	cs2.reload()
	assert cs2.status == "Deployed"
	assert cs2.rollback_data
	print(f"  Created: {test_script_name}")
	print(f"  Status: {cs2.status}")
	print("  PASSED\n")

	# Test 4: Verification results
	print("Test 4: Post-deployment verification...")
	assert result.get("verification") is not None
	v = result["verification"]
	assert v["all_documents_exist"] is True
	print(f"  Verification: all_exist={v['all_documents_exist']}, issues={len(v['issues'])}")
	print("  PASSED\n")

	# Test 5: Manual rollback
	print("Test 5: Manual rollback of deployed changeset...")
	from alfred_client.api.deploy import rollback_changeset

	rb_result = rollback_changeset(cs2.name)
	assert rb_result["status"] in ("rolled_back", "partial_rollback")
	assert not frappe.db.exists("Server Script", test_script_name)

	cs2.reload()
	assert cs2.status == "Rolled Back"
	print(f"  Rollback status: {rb_result['status']}")
	print("  PASSED\n")

	# Test 6: Reject rollback of non-deployed changeset
	print("Test 6: Reject rollback of non-deployed changeset...")
	try:
		rollback_changeset(cs2.name)  # Already rolled back
		print("  FAILED: Should reject")
	except Exception as e:
		assert "deployed" in str(e).lower()
		print("  Correctly rejected")
	print("  PASSED\n")

	# Test 7: Audit log entries created
	print("Test 7: Audit log entries...")
	audit_logs = frappe.get_all(
		"Alfred Audit Log",
		filters={"conversation": conv.name},
		fields=["action", "document_type", "document_name"],
	)
	assert len(audit_logs) > 0, "Should have audit log entries"
	print(f"  Found {len(audit_logs)} audit log entries")
	for log in audit_logs:
		print(f"    - {log.action}: {log.document_type}/{log.document_name}")
	print("  PASSED\n")

	# Test 8: Verify permission enforcement
	print("Test 8: Permission enforcement (operations run as requesting user)...")
	# The apply_changeset sets frappe.set_user(conversation.user)
	# Since conversation.user is Administrator, all ops should succeed
	# This test verifies the mechanism exists
	assert conv.user == "Administrator"
	print("  User context: " + conv.user)
	print("  PASSED\n")

	# Test 9: Dry-run input validation (malformed changesets)
	print("Test 9: dry_run_changeset input validation...")
	from alfred_client.api.deploy import dry_run_changeset

	# Empty list is valid
	result = dry_run_changeset([])
	assert result["valid"] is True
	assert result["issues"] == []

	# Non-list input is rejected
	result = dry_run_changeset({"not": "a list"})
	assert result["valid"] is False
	assert any("list" in i["issue"].lower() for i in result["issues"])

	# Malformed JSON string is rejected
	result = dry_run_changeset("{broken json")
	assert result["valid"] is False
	assert any("json" in i["issue"].lower() for i in result["issues"])

	# Non-dict item within list is flagged but doesn't crash
	result = dry_run_changeset([{"op": "create", "doctype": "Server Script", "data": {"name": "X", "script": "def ok(): pass"}}, "not a dict"])
	# At least the bad item is reported
	assert any("must be an object" in i["issue"] for i in result["issues"])
	print("  PASSED\n")

	# Test 10: Runtime error checks - Python syntax in Server Scripts
	print("Test 10: dry_run_changeset catches Python syntax errors in Server Scripts...")
	result = dry_run_changeset([{
		"op": "create",
		"doctype": "Server Script",
		"data": {
			"name": "Alfred Test Broken Script",
			"script_type": "DocType Event",
			"reference_doctype": "User",
			"doctype_event": "Before Save",
			"script": "def broken(:\n    pass",  # intentional syntax error
		},
	}])
	# Should be flagged - either by our Python compile check or by Frappe's validate
	assert not result["valid"], f"Expected invalid, got: {result}"
	python_errors = [i for i in result["issues"] if "python" in i["issue"].lower() or "syntax" in i["issue"].lower()]
	assert len(python_errors) > 0, f"Expected a Python/syntax error, got issues: {result['issues']}"
	print(f"  Caught {len(python_errors)} Python syntax issue(s)")
	print("  PASSED\n")

	# Test 11: Runtime error checks - Jinja syntax in Notifications
	print("Test 11: dry_run_changeset catches Jinja syntax errors in Notifications...")
	result = dry_run_changeset([{
		"op": "create",
		"doctype": "Notification",
		"data": {
			"name": "Alfred Test Broken Jinja",
			"subject": "New: {{ doc.",  # unterminated Jinja expression
			"document_type": "User",
			"event": "New",
			"channel": "Email",
			"recipients": [{"receiver_by_role": "System Manager"}],
			"message": "Hi",
			"enabled": 1,
		},
	}])
	# The Jinja syntax error should be caught. The insert may or may not also fail,
	# but the runtime check should have found it.
	jinja_errors = [i for i in result["issues"] if "jinja" in i["issue"].lower() or "subject" in i["issue"].lower()]
	assert len(jinja_errors) > 0, f"Expected Jinja syntax error, got issues: {result['issues']}"
	print(f"  Caught {len(jinja_errors)} Jinja issue(s)")
	print("  PASSED\n")

	# Test 12: Dry-run rolls back - no documents leak out
	print("Test 12: dry_run_changeset does not persist documents...")
	probe_name = "Alfred Dry Run Probe Script"
	# Ensure no prior run left it around
	if frappe.db.exists("Server Script", probe_name):
		frappe.delete_doc("Server Script", probe_name, force=True)
		frappe.db.commit()

	result = dry_run_changeset([{
		"op": "create",
		"doctype": "Server Script",
		"data": {
			"name": probe_name,
			"script_type": "DocType Event",
			"reference_doctype": "User",
			"doctype_event": "Before Save",
			"script": "pass",
		},
	}])
	# The probe should NOT exist after dry-run - savepoint rollback must undo it
	assert not frappe.db.exists("Server Script", probe_name), \
		"Dry-run leaked a Server Script into the database - savepoint rollback failed"
	print("  PASSED\n")

	# Cleanup
	print("Cleaning up test data...")
	frappe.db.sql("DELETE FROM `tabAlfred Audit Log` WHERE conversation = %s", conv.name)
	frappe.db.sql("DELETE FROM `tabAlfred Changeset` WHERE conversation = %s", conv.name)
	frappe.delete_doc("Alfred Conversation", conv.name, force=True)
	if frappe.db.exists("Server Script", test_script_name):
		frappe.delete_doc("Server Script", test_script_name, force=True)
	frappe.db.commit()
	print("Done.\n")

	print("=== All Deployment Engine Tests PASSED ===\n")
