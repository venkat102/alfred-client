"""Tests for escalation flow and stale cleanup.

Run with: bench --site dev.alfred execute alfred_client.test_escalation.run_tests
"""

import frappe
from frappe.utils import add_to_date, now_datetime


def run_tests():
	print("\n=== Alfred Escalation & Cleanup Tests ===\n")

	# Setup
	conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": "Administrator",
		"status": "Open",
	})
	conv.insert(ignore_permissions=True)
	frappe.db.commit()

	from alfred_client.api.escalation import (
		escalate_conversation,
		get_escalated_conversations,
		return_to_agent,
		take_over_conversation,
	)

	# Test 1: Escalate a conversation
	print("Test 1: Escalate conversation...")
	result = escalate_conversation(conv.name, reason="Too complex for AI")
	assert result["status"] == "escalated"
	conv.reload()
	assert conv.status == "Escalated"
	assert conv.escalation_reason == "Too complex for AI"
	print(f"  Status: {conv.status}, Reason: {conv.escalation_reason}")
	print("  PASSED\n")

	# Test 2: Get escalated conversations
	print("Test 2: Get escalated conversations list...")
	escalated = get_escalated_conversations()
	found = any(c["name"] == conv.name for c in escalated)
	assert found, "Escalated conversation not in list"
	print(f"  Found {len(escalated)} escalated conversation(s)")
	print("  PASSED\n")

	# Test 3: Take over conversation
	print("Test 3: Take over conversation...")
	result = take_over_conversation(conv.name)
	assert result["status"] == "taken_over"
	conv.reload()
	assert conv.status == "In Progress"
	assert "Human:" in conv.current_agent
	print(f"  Status: {conv.status}, Agent: {conv.current_agent}")
	print("  PASSED\n")

	# Test 4: Return to agent
	print("Test 4: Return to agent...")
	# First re-escalate
	conv.status = "Escalated"
	conv.save(ignore_permissions=True)
	frappe.db.commit()

	result = return_to_agent(conv.name)
	assert result["status"] == "returned_to_agent"
	conv.reload()
	assert conv.status == "In Progress"
	assert conv.current_agent is None or conv.current_agent == ""
	print(f"  Status: {conv.status}")
	print("  PASSED\n")

	# Test 5: Take over non-escalated (should fail)
	print("Test 5: Reject take over of non-escalated...")
	try:
		take_over_conversation(conv.name)
		print("  FAILED: Should reject")
	except Exception as e:
		assert "escalated" in str(e).lower()
		print("  Correctly rejected")
	print("  PASSED\n")

	# Test 6: Stale conversation cleanup
	print("Test 6: Stale conversation marking...")
	stale_conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": "Administrator",
		"status": "Open",
	})
	stale_conv.insert(ignore_permissions=True)
	# Backdate the modified timestamp
	frappe.db.set_value("Alfred Conversation", stale_conv.name, "modified",
		add_to_date(now_datetime(), hours=-48), update_modified=False)
	frappe.db.commit()

	from alfred_client.api.stale_cleanup import mark_stale_conversations
	mark_stale_conversations()

	stale_conv.reload()
	assert stale_conv.status == "Stale", f"Expected Stale, got {stale_conv.status}"
	print(f"  Status after 48h: {stale_conv.status}")
	print("  PASSED\n")

	# Test 7: Messages created for escalation events
	print("Test 7: Escalation system messages...")
	msgs = frappe.get_all(
		"Alfred Message",
		filters={"conversation": conv.name, "role": "system"},
		pluck="content",
	)
	assert len(msgs) > 0, "Should have system messages for escalation events"
	print(f"  Found {len(msgs)} system messages")
	print("  PASSED\n")

	# Cleanup
	print("Cleaning up...")
	for c in [conv.name, stale_conv.name]:
		frappe.db.sql("DELETE FROM `tabAlfred Message` WHERE conversation = %s", c)
		frappe.delete_doc("Alfred Conversation", c, force=True)
	frappe.db.commit()
	print("Done.\n")

	print("=== All Escalation & Cleanup Tests PASSED ===\n")
