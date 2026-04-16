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
	# TOOL_REGISTRY grew post Phase 1 tool consolidation: dry_run_changeset
	# (pipeline validation) and the consolidated lookup_doctype +
	# lookup_pattern (Framework KG) joined the original 9. FKB Phase A added
	# lookup_frappe_knowledge. FKB Phase B.5 added get_site_customization_detail.
	# Keep this set in sync with TOOL_REGISTRY in alfred_client/mcp/tools.py.
	print("Test 1: tools/list returns all 14 tools...")
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
		# Phase 1: pipeline validation
		"dry_run_changeset",
		# Phase 1b: consolidated Framework KG tools
		"lookup_doctype", "lookup_pattern",
		# FKB Phase A: platform knowledge base
		"lookup_frappe_knowledge",
		# FKB Phase B.5: per-DocType deep recon
		"get_site_customization_detail",
	}
	assert tool_names == expected_tools, f"Expected {expected_tools}, got {tool_names}"
	# Every tool must carry a non-empty docstring so agents can decide when
	# to call it - empty docs were a documented drift failure in phase 1 QA.
	for t in tools:
		assert t.get("description"), f"Tool {t['name']} has no description"
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

	# Test 16: dry_run_changeset - empty list is valid
	print("Test 16: tools/call dry_run_changeset (empty list)...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "dry_run_changeset", "arguments": {"changes": "[]"}},
		"id": 16,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["valid"] is True
	assert content["issues"] == []
	print("  Empty changeset valid: OK")

	# Dry-run an obviously-broken Notification - should flag missing doctype
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {
			"name": "dry_run_changeset",
			"arguments": {
				"changes": json.dumps([{
					"op": "create",
					"doctype": "NonExistentDocType_XYZ",
					"data": {"name": "X", "doctype": "NonExistentDocType_XYZ"},
				}])
			},
		},
		"id": 17,
	})
	content = json.loads(response["result"]["content"][0]["text"])
	assert content["valid"] is False
	assert any("does not exist" in (i.get("issue", "")) for i in content.get("issues", []))
	print("  Invalid doctype flagged: OK")
	print("  PASSED\n")

	# Test 17: lookup_doctype (framework layer) - User is a core framework DocType
	print("Test 17: tools/call lookup_doctype layer=framework...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {
			"name": "lookup_doctype",
			"arguments": {"name": "User", "layer": "framework"},
		},
		"id": 18,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	# Framework KG may or may not have the record depending on whether build
	# has run - either a record with fields or a not_found error is acceptable
	# for a smoke test. A third state (unexpected key shape) should fail.
	assert "error" in content or "fields" in content or "name" in content
	print(f"  layer=framework shape: {list(content.keys())[:4]}")

	# Site layer should always return a record for User
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {
			"name": "lookup_doctype",
			"arguments": {"name": "User", "layer": "site"},
		},
		"id": 19,
	})
	content = json.loads(response["result"]["content"][0]["text"])
	assert content.get("doctype") == "User" or content.get("name") == "User"
	assert "fields" in content
	print(f"  layer=site returned {len(content['fields'])} fields")

	# Invalid layer value must be rejected
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {
			"name": "lookup_doctype",
			"arguments": {"name": "User", "layer": "banana"},
		},
		"id": 20,
	})
	content = json.loads(response["result"]["content"][0]["text"])
	assert content.get("error") == "invalid_argument"
	print("  invalid layer rejected: OK")
	print("  PASSED\n")

	# Test 18: lookup_pattern kind=list
	print("Test 18: tools/call lookup_pattern kind=list...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "lookup_pattern", "arguments": {"query": "", "kind": "list"}},
		"id": 21,
	})
	assert "result" in response
	content = json.loads(response["result"]["content"][0]["text"])
	assert "patterns" in content
	# Should have at least the 5 starter patterns (approval_notification etc)
	# but we only assert presence, not count, so the test survives expansions.
	assert isinstance(content["patterns"], list)
	print(f"  {len(content['patterns'])} pattern(s) registered")

	# Invalid kind must be rejected
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "lookup_pattern", "arguments": {"query": "x", "kind": "banana"}},
		"id": 22,
	})
	content = json.loads(response["result"]["content"][0]["text"])
	assert content.get("error") == "invalid_argument"
	print("  invalid kind rejected: OK")
	print("  PASSED\n")

	# Test 19: get_site_customization_detail - known DocType returns deep shape
	# Using "ToDo" because it's a core built-in DocType present on every Frappe
	# site and the permission system lets us read it without special setup.
	print("Test 19: tools/call get_site_customization_detail (ToDo)...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_site_customization_detail",
		            "arguments": {"doctype": "ToDo"}},
		"id": 23,
	})
	assert "result" in response, f"Error: {response.get('error')}"
	content = json.loads(response["result"]["content"][0]["text"])
	# May or may not have customizations, but the shape must match regardless.
	assert content.get("doctype") == "ToDo"
	for key in ("custom_fields", "server_scripts", "workflows",
	            "notifications", "client_scripts"):
		assert key in content, f"missing key {key} in response"
		assert isinstance(content[key], list), f"{key} must be a list"
	print(f"  shape OK: "
	      f"cf={len(content['custom_fields'])} "
	      f"ss={len(content['server_scripts'])} "
	      f"wf={len(content['workflows'])} "
	      f"nt={len(content['notifications'])} "
	      f"cs={len(content['client_scripts'])}")
	print("  PASSED\n")

	# Test 20: get_site_customization_detail - unknown DocType returns not_found
	print("Test 20: tools/call get_site_customization_detail (unknown)...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_site_customization_detail",
		            "arguments": {"doctype": "Nonexistent DocType XYZ"}},
		"id": 24,
	})
	content = json.loads(response["result"]["content"][0]["text"])
	assert content.get("error") == "not_found", f"Expected not_found, got {content}"
	print("  unknown DocType correctly rejected")
	print("  PASSED\n")

	# Test 21: get_site_customization_detail - invalid argument rejected
	print("Test 21: tools/call get_site_customization_detail (empty doctype)...")
	response = handle_mcp_request({
		"jsonrpc": "2.0",
		"method": "tools/call",
		"params": {"name": "get_site_customization_detail",
		            "arguments": {"doctype": ""}},
		"id": 25,
	})
	content = json.loads(response["result"]["content"][0]["text"])
	assert content.get("error") == "invalid_argument", f"Expected invalid_argument, got {content}"
	print("  empty doctype correctly rejected")
	print("  PASSED\n")

	# Test 22: get_site_customization_detail - Server Script bodies are truncated
	# Set up a Server Script with a body longer than the truncation budget
	# (600 chars) and assert the returned body is truncated.
	print("Test 22: get_site_customization_detail truncates Server Script bodies...")
	test_script_name = "Alfred Test FKB Long Script"
	# Clean up any leftover from a prior run.
	if frappe.db.exists("Server Script", test_script_name):
		frappe.delete_doc("Server Script", test_script_name, force=True)
	long_body = "# padding " * 150  # ~1500 chars
	frappe.get_doc({
		"doctype": "Server Script",
		"name": test_script_name,
		"script_type": "DocType Event",
		"reference_doctype": "ToDo",
		"doctype_event": "Before Save",
		"script": long_body + "\nfrappe.msgprint('ok')",
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	try:
		response = handle_mcp_request({
			"jsonrpc": "2.0",
			"method": "tools/call",
			"params": {"name": "get_site_customization_detail",
			            "arguments": {"doctype": "ToDo"}},
			"id": 26,
		})
		content = json.loads(response["result"]["content"][0]["text"])
		ours = [s for s in content["server_scripts"] if s["name"] == test_script_name]
		assert ours, f"test script not found in response: {content['server_scripts']}"
		returned_script = ours[0]["script"]
		assert len(returned_script) < len(long_body), (
			f"expected truncation, got {len(returned_script)} chars"
		)
		assert returned_script.endswith("..."), (
			f"truncated string must end with '...' sentinel, got {returned_script[-20:]!r}"
		)
		print(f"  body truncated: {len(returned_script)} chars (from {len(long_body)}+)")
	finally:
		# Always clean up the fixture
		if frappe.db.exists("Server Script", test_script_name):
			frappe.delete_doc("Server Script", test_script_name, force=True)
			frappe.db.commit()
	print("  PASSED\n")

	print("=== All MCP Server Tests PASSED ===\n")
