"""Tests for the Alfred chat page backend APIs.

Run with: bench --site dev.alfred execute alfred_client.test_page.run_tests
"""

import frappe


def run_tests():
	print("\n=== Alfred Chat Page Tests ===\n")

	from alfred_client.alfred_settings.page.alfred.alfred import (
		get_conversations,
		create_conversation,
		get_messages,
		send_message,
	)

	# Test 1: Get conversations (initially may be empty)
	print("Test 1: Get conversations...")
	convs = get_conversations()
	print(f"  Found {len(convs)} conversations")
	print("  PASSED\n")

	# Test 2: Create conversation
	print("Test 2: Create conversation...")
	result = create_conversation()
	assert result["name"]
	assert result["status"] == "Open"
	print(f"  Created: {result['name']}")
	print("  PASSED\n")

	conv_name = result["name"]

	# Test 3: Get messages (empty)
	print("Test 3: Get messages (empty conversation)...")
	msgs = get_messages(conv_name)
	assert len(msgs) == 0
	print(f"  Messages: {len(msgs)}")
	print("  PASSED\n")

	# Test 4: Send message
	print("Test 4: Send message...")
	msg_result = send_message(conv_name, "Create a Book DocType with title and author")
	assert msg_result["name"]
	assert msg_result["status"] == "sent"
	print(f"  Message sent: {msg_result['name']}")
	print("  PASSED\n")

	# Test 5: Get messages (should have 1)
	print("Test 5: Get messages after send...")
	msgs = get_messages(conv_name)
	assert len(msgs) == 1
	assert msgs[0]["role"] == "user"
	assert "Book" in msgs[0]["content"]
	print(f"  Messages: {len(msgs)}, content preview: {msgs[0]['content'][:50]}")
	print("  PASSED\n")

	# Test 6: Conversation status updated
	print("Test 6: Conversation status updated to In Progress...")
	conv = frappe.get_doc("Alfred Conversation", conv_name)
	assert conv.status == "In Progress"
	print(f"  Status: {conv.status}")
	print("  PASSED\n")

	# Test 7: Page exists
	print("Test 7: Page registered in Frappe...")
	assert frappe.db.exists("Page", "alfred")
	page = frappe.get_doc("Page", "alfred")
	assert page.module == "Alfred Settings"
	print(f"  Page: {page.name}, module: {page.module}")
	print("  PASSED\n")

	# Cleanup
	print("Cleaning up...")
	frappe.db.sql("DELETE FROM `tabAlfred Message` WHERE conversation = %s", conv_name)
	frappe.delete_doc("Alfred Conversation", conv_name, force=True)
	frappe.db.commit()
	print("Done.\n")

	print("=== All Chat Page Tests PASSED ===\n")
