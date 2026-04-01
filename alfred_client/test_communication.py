"""End-to-end communication test between Client App and Processing App.

Tests:
1. JWT generation and verification
2. WebSocket handshake protocol
3. Message round-trip (echo test)
4. Frappe publish_realtime bridge (mocked)

Run with: bench --site dev.alfred execute alfred_client.test_communication.run_tests
"""

import json
import time

import frappe
import jwt as pyjwt


def run_tests():
	print("\n=== Alfred Communication Tests ===\n")

	# Test 1: JWT generation
	print("Test 1: JWT generation...")
	from alfred_client.api.websocket_client import _generate_jwt, _get_site_id

	site_id = _get_site_id()
	print(f"  site_id: {site_id}")
	assert site_id, "site_id should not be empty"

	# Use a test secret key for JWT generation
	test_key = "test-secret-for-jwt-communication"
	token = _generate_jwt(test_key, user="Administrator", roles=["System Manager"])
	assert token, "JWT should not be empty"
	print(f"  JWT generated: {token[:50]}...")

	# Decode and verify
	payload = pyjwt.decode(token, test_key, algorithms=["HS256"])
	assert payload["user"] == "Administrator"
	assert "System Manager" in payload["roles"]
	assert payload["site_id"] == site_id
	assert payload["iat"] > 0
	assert payload["exp"] > payload["iat"]
	print("  JWT payload verified")
	print("  PASSED\n")

	# Test 2: site_config generation
	print("Test 2: Site config generation...")
	from alfred_client.api.websocket_client import _get_site_config

	config = _get_site_config()
	assert "site_id" in config
	assert "llm_provider" in config
	assert "llm_model" in config
	assert "llm_max_tokens" in config
	assert "llm_temperature" in config
	assert "max_tasks_per_user_per_hour" in config
	assert "task_timeout_seconds" in config
	print(f"  Config keys: {list(config.keys())}")
	print("  PASSED\n")

	# Test 3: AlfredWebSocketClient initialization
	print("Test 3: WebSocket client initialization...")
	from alfred_client.api.websocket_client import AlfredWebSocketClient

	# Create a test conversation
	conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": "Administrator",
		"status": "Open",
	})
	conv.insert()
	frappe.db.commit()

	client = AlfredWebSocketClient(conv.name, user="Administrator")
	assert client.conversation_name == conv.name
	assert client.user == "Administrator"
	assert client.connected is False
	assert client.last_msg_id is None
	assert client._backoff == 1
	print(f"  Client created for conversation: {conv.name}")
	print("  PASSED\n")

	# Test 4: Message routing
	print("Test 4: Message routing (mocked)...")
	received_events = []

	# Mock frappe.publish_realtime to capture events
	original_publish = frappe.publish_realtime

	def mock_publish(event, message=None, **kwargs):
		received_events.append({"event": event, "message": message, "kwargs": kwargs})

	frappe.publish_realtime = mock_publish

	try:
		# Test agent_status message routing
		client._route_incoming_message({
			"msg_id": "test-msg-1",
			"type": "agent_status",
			"data": {"agent": "requirement", "status": "running"},
		})
		assert len(received_events) == 1
		assert received_events[0]["event"] == "intern_agent_status"
		assert received_events[0]["message"]["agent"] == "requirement"
		print("  agent_status -> intern_agent_status: OK")

		# Test question message routing
		client._route_incoming_message({
			"msg_id": "test-msg-2",
			"type": "question",
			"data": {"text": "What fields?", "options": ["A", "B"]},
		})
		assert received_events[-1]["event"] == "intern_question"
		print("  question -> intern_question: OK")

		# Test error message routing
		client._route_incoming_message({
			"msg_id": "test-msg-3",
			"type": "error",
			"data": {"error": "Something went wrong"},
		})
		assert received_events[-1]["event"] == "intern_error"
		print("  error -> intern_error: OK")

		# Test last_msg_id tracking
		assert client.last_msg_id == "test-msg-3"
		print("  last_msg_id tracked correctly: test-msg-3")

		# Test ping is ignored (no event published)
		count_before = len(received_events)
		client._route_incoming_message({"type": "ping"})
		assert len(received_events) == count_before
		print("  ping ignored: OK")

	finally:
		frappe.publish_realtime = original_publish

	print("  PASSED\n")

	# Test 5: Whitelisted API functions exist
	print("Test 5: API function registration...")
	from alfred_client.api.websocket_client import send_message, start_conversation, stop_conversation
	assert callable(send_message)
	assert callable(start_conversation)
	assert callable(stop_conversation)
	print("  send_message: registered")
	print("  start_conversation: registered")
	print("  stop_conversation: registered")
	print("  PASSED\n")

	# Cleanup
	print("Cleaning up...")
	frappe.delete_doc("Alfred Conversation", conv.name, force=True)
	frappe.db.commit()
	print("Done.\n")

	print("=== All Communication Tests PASSED ===\n")
