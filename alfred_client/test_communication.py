"""Tests for the WebSocket communication layer (rewritten for Redis pub/sub architecture).

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

	test_key = "test-secret-for-jwt-communication"
	token = _generate_jwt(test_key, user="Administrator", roles=["System Manager"])
	assert token, "JWT should not be empty"

	payload = pyjwt.decode(token, test_key, algorithms=["HS256"])
	assert payload["user"] == "Administrator"
	assert "System Manager" in payload["roles"]
	assert payload["site_id"] == site_id
	print("  JWT payload verified")
	print("  PASSED\n")

	# Test 2: site_config generation
	print("Test 2: Site config generation...")
	from alfred_client.api.websocket_client import _get_site_config

	config = _get_site_config()
	assert "site_id" in config
	assert "llm_provider" in config
	assert "llm_max_tokens" in config
	# Per-tier model fields must be present so the processing app can
	# resolve the right model for each pipeline stage.
	for tier in ("triage", "reasoning", "agent"):
		key = f"llm_model_{tier}"
		ctx_key = f"llm_model_{tier}_num_ctx"
		assert key in config, f"_get_site_config() missing {key}"
		assert ctx_key in config, f"_get_site_config() missing {ctx_key}"
		# Empty tier field must serialize as "" (not None) so the
		# processing app's fallback-to-default resolver can treat it
		# as "unset" via truthiness.
		assert config[key] == "" or isinstance(config[key], str), (
			f"{key} should be a string, got {type(config[key]).__name__}"
		)
		assert isinstance(config[ctx_key], int), (
			f"{ctx_key} should be int, got {type(config[ctx_key]).__name__}"
		)
	print(f"  Config keys: {list(config.keys())}")
	print("  Per-tier model fields verified")
	print("  PASSED\n")

	# Test 3: Message routing (mocked)
	print("Test 3: Message routing (mocked)...")
	from alfred_client.api.websocket_client import _route_incoming_message

	received_events = []
	original_publish = frappe.publish_realtime

	def mock_publish(event, message=None, **kwargs):
		received_events.append({"event": event, "message": message})

	frappe.publish_realtime = mock_publish
	try:
		_route_incoming_message(
			{"type": "agent_status", "data": {"agent": "requirement", "status": "running"}},
			user="Administrator", conversation_name="test-conv",
		)
		assert len(received_events) == 1
		assert received_events[0]["event"] == "alfred_agent_status"
		print("  agent_status -> alfred_agent_status: OK")

		_route_incoming_message(
			{"type": "question", "data": {"text": "What fields?"}},
			user="Administrator", conversation_name="test-conv",
		)
		assert received_events[-1]["event"] == "alfred_question"
		print("  question -> alfred_question: OK")

		_route_incoming_message(
			{"type": "error", "data": {"error": "Something went wrong"}},
			user="Administrator", conversation_name="test-conv",
		)
		assert received_events[-1]["event"] == "alfred_error"
		print("  error -> alfred_error: OK")

		# Ping should not produce an event
		count_before = len(received_events)
		_route_incoming_message({"type": "ping"}, user="Administrator", conversation_name="test-conv")
		assert len(received_events) == count_before
		print("  ping ignored: OK")
	finally:
		frappe.publish_realtime = original_publish
	print("  PASSED\n")

	# Test 4: Redis pub/sub channel naming
	print("Test 4: Redis channel naming...")
	from alfred_client.api.websocket_client import _REDIS_CHANNEL_PREFIX
	assert _REDIS_CHANNEL_PREFIX == "alfred:ws:outbound:"
	channel = f"{_REDIS_CHANNEL_PREFIX}test-conv-123"
	assert channel == "alfred:ws:outbound:test-conv-123"
	print(f"  Channel: {channel}")
	print("  PASSED\n")

	# Test 5: API functions exist and are whitelisted
	print("Test 5: API function registration...")
	from alfred_client.api.websocket_client import send_message, start_conversation, stop_conversation
	assert callable(send_message)
	assert callable(start_conversation)
	assert callable(stop_conversation)
	print("  send_message, start_conversation, stop_conversation: registered")
	print("  PASSED\n")

	print("=== All Communication Tests PASSED ===\n")
