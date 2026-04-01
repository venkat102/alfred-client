"""Tests for the MCP Server and tools.

Run with: bench --site dev.alfred execute alfred_client.test_mcp.run_tests
"""

import json

import frappe

from alfred_client.mcp.server import handle_mcp_request
from alfred_client.mcp.transport import is_mcp_message, route_websocket_message


def run_tests():
	print("\n=== Alfred MCP Server Tests ===\n")

	# Test 1: tools/list
	print("Test 1: tools/list returns all 9 tools...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/list",
		"id": 1,
	})
	assert response["jsonrpc"] == "2.0"
	assert response["id"] == 1
	assert "result" in response
	tools = response["result"]["tools"]
	tool_names = {t["name"] for t in tools}
	expected_tools = {
		"get_site_info", "get_doctypes", "get_doctype_schema",
		"get_existing_customizations", "get_user_context",
		"check_permission", "validate_name_available",
		"has_active_workflow", "check_has_records",
	}
	assert tool_names == expected_tools, f"Expected {expected_tools}, got {tool_names}"
	print(f"  Found {len(tools)} tools: {', '.join(sorted(tool_names))}")
	print("  PASSED\n")

	# Test 2: get_site_info
	print("Test 2: tools/call get_site_info...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_site_info"},
		"id": 2,
	})
	assert "result" in response, f"Error: {response.get('error')}"
	content = json.loads(response["result"]["content"][0]["text"])
	assert "frappe_version" in content
	assert "installed_apps" in content
	print(f"  Frappe version: {content['frappe_version']}")
	print(f"  Apps: {[a['name'] for a in content['installed_apps']]}")
	print("  PASSED\n")

	# Test 3: get_doctypes
	print("Test 3: tools/call get_doctypes...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_doctypes"},
		"id": 3,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert "doctypes" in content
	assert content["count"] > 0
	print(f"  Found {content['count']} DocTypes")
	print("  PASSED\n")

	# Test 4: get_doctypes with module filter
	print("Test 4: tools/call get_doctypes with module filter...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_doctypes", "arguments": {"module": "Core"}},
		"id": 4,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	for dt in content["doctypes"]:
		assert dt["module"] == "Core", f"Expected Core module, got {dt['module']}"
	print(f"  Core module: {content['count']} DocTypes")
	print("  PASSED\n")

	# Test 5: get_doctype_schema
	print("Test 5: tools/call get_doctype_schema (User)...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_doctype_schema", "arguments": {"doctype": "User"}},
		"id": 5,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["doctype"] == "User"
	assert len(content["fields"]) > 0
	assert "permissions" in content
	print(f"  User DocType: {content['field_count']} fields, module={content['module']}")
	print("  PASSED\n")

	# Test 6: check_permission
	print("Test 6: tools/call check_permission...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "check_permission", "arguments": {"doctype": "User", "action": "read"}},
		"id": 6,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["permitted"] is True  # Administrator has all permissions
	assert content["action"] == "read"
	print(f"  User read permission: {content['permitted']}")
	print("  PASSED\n")

	# Test 7: validate_name_available
	print("Test 7: tools/call validate_name_available...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "validate_name_available", "arguments": {"doctype": "User", "name": "Administrator"}},
		"id": 7,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["available"] is False  # Administrator exists
	assert content["exists"] is True
	print(f"  Administrator exists: {content['exists']}")

	# Check non-existent
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "validate_name_available", "arguments": {"doctype": "DocType", "name": "NonExistentDocType12345"}},
		"id": 8,
	})
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["available"] is True
	print(f"  NonExistentDocType12345 available: {content['available']}")
	print("  PASSED\n")

	# Test 8: has_active_workflow
	print("Test 8: tools/call has_active_workflow...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "has_active_workflow", "arguments": {"doctype": "User"}},
		"id": 9,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert "has_active_workflow" in content
	print(f"  User has active workflow: {content['has_active_workflow']}")
	print("  PASSED\n")

	# Test 9: get_user_context
	print("Test 9: tools/call get_user_context...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_user_context"},
		"id": 10,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["user"] == "Administrator"
	assert "System Manager" in content["roles"]
	print(f"  User: {content['user']}, Roles: {content['roles'][:3]}...")
	print("  PASSED\n")

	# Test 10: Unknown method
	print("Test 10: Unknown method returns error...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "unknown/method",
		"id": 11,
	})
	assert "error" in response
	assert response["error"]["code"] == -32601  # METHOD_NOT_FOUND
	print(f"  Error: {response['error']['message']}")
	print("  PASSED\n")

	# Test 11: Unknown tool
	print("Test 11: Unknown tool returns error...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "nonexistent_tool"},
		"id": 12,
	})
	assert "error" in response
	assert response["error"]["code"] == -32601
	print(f"  Error: {response['error']['message']}")
	print("  PASSED\n")

	# Test 12: Invalid JSON-RPC version
	print("Test 12: Invalid JSON-RPC version...")
	response = handle_mcp_request({
		"jsonrpc": "1.0",
		"method": "tools/list",
		"id": 13,
	})
	assert "error" in response
	assert response["error"]["code"] == -32600
	print("  PASSED\n")

	# Test 13: Transport routing
	print("Test 13: Transport message routing...")
	# MCP message
	msg_type, data = route_websocket_message(json.dumps({
		"jsonrpc": "2.0", "method": "tools/list", "id": 1,
	}))
	assert msg_type == "mcp"
	assert "result" in data

	# Custom message
	msg_type, data = route_websocket_message(json.dumps({
		"type": "prompt", "msg_id": "123", "data": {"text": "hello"},
	}))
	assert msg_type == "custom"
	assert data["type"] == "prompt"

	# Invalid JSON
	msg_type, data = route_websocket_message("not valid json{{{")
	assert msg_type == "error"
	print("  MCP routing: OK, Custom routing: OK, Error handling: OK")
	print("  PASSED\n")

	# Test 14: is_mcp_message
	print("Test 14: Message classification...")
	assert is_mcp_message({"jsonrpc": "2.0", "method": "tools/list"}) is True
	assert is_mcp_message({"type": "prompt", "data": {}}) is False
	assert is_mcp_message({}) is False
	print("  PASSED\n")

	# Test 15: check_has_records
	print("Test 15: tools/call check_has_records...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "check_has_records", "arguments": {"doctype": "User"}},
		"id": 15,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["has_records"] is True  # Users always exist
	assert content["count"] > 0
	print(f"  User records: {content['count']}")
	print("  PASSED\n")

	print("=== All MCP Server Tests PASSED ===\n")
