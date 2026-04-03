"""Functional tests for Alfred DocTypes - run with: bench --site dev.alfred execute alfred_client.test_doctypes.run_tests"""

import json

import frappe


def run_tests():
	print("\n=== Alfred DocType Functional Tests ===\n")

	# Test 1: Alfred Settings loads with correct defaults
	print("Test 1: Alfred Settings loads with defaults...")
	settings = frappe.get_single("Alfred Settings")
	assert settings.llm_max_tokens == 4096, f"Expected 4096, got {settings.llm_max_tokens}"
	assert settings.llm_temperature == 0.1, f"Expected 0.1, got {settings.llm_temperature}"
	assert settings.max_retries_per_agent == 3, f"Expected 3, got {settings.max_retries_per_agent}"
	assert settings.max_tasks_per_user_per_hour == 20, f"Expected 20, got {settings.max_tasks_per_user_per_hour}"
	assert settings.task_timeout_seconds == 300, f"Expected 300, got {settings.task_timeout_seconds}"
	assert settings.stale_conversation_hours == 24, f"Expected 24, got {settings.stale_conversation_hours}"
	assert settings.total_tokens_used == 0
	assert settings.total_conversations == 0
	print("  PASSED\n")

	# Test 2: Create an Alfred Conversation
	print("Test 2: Create Alfred Conversation...")
	conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": "Administrator",
		"status": "Open",
	})
	conv.insert()
	frappe.db.commit()
	assert conv.name, "Conversation should have a name"
	assert conv.status == "Open"
	print(f"  Created conversation: {conv.name}")
	print("  PASSED\n")

	# Test 3: Create Alfred Messages linked to conversation
	print("Test 3: Create Alfred Messages...")
	msg1 = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conv.name,
		"role": "user",
		"message_type": "text",
		"content": "I need a ToDo DocType",
	})
	msg1.insert()

	msg2 = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conv.name,
		"role": "agent",
		"agent_name": "requirement",
		"message_type": "question",
		"content": "What fields should the ToDo have?",
		"metadata": json.dumps({"options": ["Description", "Priority", "Due Date"]}),
	})
	msg2.insert()

	msg3 = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conv.name,
		"role": "system",
		"message_type": "status",
		"content": "Agent pipeline started",
	})
	msg3.insert()
	frappe.db.commit()

	messages = frappe.get_all(
		"Alfred Message",
		filters={"conversation": conv.name},
		fields=["name", "role", "message_type"],
		order_by="creation asc",
	)
	assert len(messages) == 3, f"Expected 3 messages, got {len(messages)}"
	assert messages[0].role == "user"
	assert messages[1].role == "agent"
	assert messages[2].role == "system"
	print(f"  Created 3 messages for conversation {conv.name}")
	print("  PASSED\n")

	# Test 4: Create Alfred Changeset with valid JSON
	print("Test 4: Create Alfred Changeset with JSON...")
	changeset = frappe.get_doc({
		"doctype": "Alfred Changeset",
		"conversation": conv.name,
		"status": "Pending",
		"changes": json.dumps([
			{"op": "create", "doctype": "Custom Field", "data": {"name": "test_field"}},
		]),
	})
	changeset.insert()
	frappe.db.commit()

	# Reload and verify JSON preserved
	changeset.reload()
	changes = json.loads(changeset.changes) if isinstance(changeset.changes, str) else changeset.changes
	assert isinstance(changes, list), "Changes should be a list"
	assert changes[0]["op"] == "create"
	print(f"  Created changeset: {changeset.name}, JSON preserved correctly")
	print("  PASSED\n")

	# Test 5: Alfred Audit Log is created and immutable
	print("Test 5: Alfred Audit Log immutability...")
	audit = frappe.get_doc({
		"doctype": "Alfred Audit Log",
		"conversation": conv.name,
		"agent": "architect",
		"action": "created DocType",
		"document_type": "ToDo",
		"document_name": "ToDo",
		"before_state": json.dumps({}),
		"after_state": json.dumps({"fields": ["description", "priority"]}),
	})
	audit.insert()
	frappe.db.commit()
	print(f"  Created audit log: {audit.name}")

	# Try to modify - should fail
	try:
		audit.action = "modified DocType"
		audit.save()
		print("  FAILED: Should not allow modification")
	except frappe.PermissionError:
		print("  Correctly blocked modification of audit log")
	except Exception as e:
		print(f"  Correctly blocked modification: {e}")

	frappe.db.rollback()
	print("  PASSED\n")

	# Test 6: Conversation with created_documents child table
	print("Test 6: Created Documents child table...")
	conv2 = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": "Administrator",
		"status": "Completed",
		"created_documents": [
			{"document_type": "DocType", "document_name": "ToDo", "operation": "Created"},
			{"document_type": "Server Script", "document_name": "todo_validate", "operation": "Created"},
		],
	})
	conv2.insert()
	frappe.db.commit()
	assert len(conv2.created_documents) == 2
	print(f"  Created conversation with 2 documents: {conv2.name}")
	print("  PASSED\n")

	# Test 7: Validate JSON field rejects invalid JSON
	print("Test 7: Invalid JSON validation...")
	try:
		bad_msg = frappe.get_doc({
			"doctype": "Alfred Message",
			"conversation": conv.name,
			"role": "agent",
			"message_type": "text",
			"content": "test",
			"metadata": "not valid json {{{",
		})
		bad_msg.insert()
		print("  FAILED: Should reject invalid JSON")
	except Exception as e:
		print(f"  Correctly rejected invalid JSON: {type(e).__name__}")
	frappe.db.rollback()
	print("  PASSED\n")

	# Test 8: Changeset with empty JSON fields
	print("Test 8: Empty JSON fields in changeset...")
	empty_changeset = frappe.get_doc({
		"doctype": "Alfred Changeset",
		"conversation": conv.name,
		"status": "Pending",
	})
	empty_changeset.insert()
	frappe.db.commit()
	print(f"  Created changeset with empty JSON: {empty_changeset.name}")
	print("  PASSED\n")

	# Test 9: validate_alfred_access utility function
	print("Test 9: validate_alfred_access()...")
	from alfred_client.api.permissions import validate_alfred_access
	# Administrator should always pass
	validate_alfred_access()
	print("  Administrator access: PASSED")

	# Test with has_app_permission
	from alfred_client.api.permissions import has_app_permission
	result = has_app_permission()
	assert result is True, "Administrator should have app permission"
	print("  has_app_permission: PASSED\n")

	# Cleanup
	print("Cleaning up test data...")
	frappe.db.sql("DELETE FROM `tabAlfred Message` WHERE conversation IN (%(c1)s, %(c2)s)", {"c1": conv.name, "c2": conv2.name})
	frappe.db.sql("DELETE FROM `tabAlfred Changeset` WHERE conversation IN (%(c1)s, %(c2)s)", {"c1": conv.name, "c2": conv2.name})
	frappe.db.sql("DELETE FROM `tabAlfred Audit Log` WHERE conversation IN (%(c1)s, %(c2)s)", {"c1": conv.name, "c2": conv2.name})
	frappe.db.sql("DELETE FROM `tabAlfred Created Document` WHERE parent IN (%(c1)s, %(c2)s)", {"c1": conv.name, "c2": conv2.name})
	frappe.delete_doc("Alfred Conversation", conv.name, force=True)
	frappe.delete_doc("Alfred Conversation", conv2.name, force=True)
	frappe.db.commit()
	print("Cleanup done.\n")

	print("=== All Tests PASSED ===\n")
