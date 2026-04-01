"""MCP Server for the Alfred Client App.

Handles incoming JSON-RPC 2.0 requests from the Processing App's agents.
Routes requests to the appropriate tool handler and returns JSON-RPC responses.

The MCP server runs within the Frappe user's session context — all tool calls
automatically enforce Frappe's permission system.
"""

import json
import logging
from typing import Any

from alfred_client.mcp.tools import TOOL_REGISTRY

logger = logging.getLogger("alfred.mcp.server")

# JSON-RPC 2.0 Error Codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _jsonrpc_error(id_val: Any, code: int, message: str, data: Any = None) -> dict:
	"""Build a JSON-RPC 2.0 error response."""
	error = {"code": code, "message": message}
	if data is not None:
		error["data"] = data
	return {"jsonrpc": "2.0", "id": id_val, "error": error}


def _jsonrpc_result(id_val: Any, result: Any) -> dict:
	"""Build a JSON-RPC 2.0 success response."""
	return {"jsonrpc": "2.0", "id": id_val, "result": result}


def handle_mcp_request(raw_message: str | dict) -> dict:
	"""Process an incoming MCP JSON-RPC 2.0 request.

	Args:
		raw_message: Either a JSON string or already-parsed dict.

	Returns:
		JSON-RPC 2.0 response dict.
	"""
	# Parse if string
	if isinstance(raw_message, str):
		try:
			message = json.loads(raw_message)
		except json.JSONDecodeError as e:
			return _jsonrpc_error(None, PARSE_ERROR, f"Parse error: {e}")
	else:
		message = raw_message

	# Validate JSON-RPC 2.0 format
	if not isinstance(message, dict):
		return _jsonrpc_error(None, INVALID_REQUEST, "Request must be a JSON object")

	if message.get("jsonrpc") != "2.0":
		return _jsonrpc_error(
			message.get("id"),
			INVALID_REQUEST,
			"Invalid JSON-RPC version. Expected '2.0'.",
		)

	request_id = message.get("id")
	method = message.get("method")

	if not method:
		return _jsonrpc_error(request_id, INVALID_REQUEST, "Missing 'method' field")

	# MCP method routing
	if method == "tools/list":
		return _handle_tools_list(request_id)
	elif method == "tools/call":
		params = message.get("params", {})
		return _handle_tools_call(request_id, params)
	else:
		return _jsonrpc_error(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")


def _handle_tools_list(request_id: Any) -> dict:
	"""Return the list of available MCP tools with their descriptions."""
	tools = []
	for name, func in TOOL_REGISTRY.items():
		tools.append({
			"name": name,
			"description": func.__doc__ or "",
		})
	return _jsonrpc_result(request_id, {"tools": tools})


def _handle_tools_call(request_id: Any, params: dict) -> dict:
	"""Execute a specific MCP tool.

	Params:
		name: Tool name (required)
		arguments: Dict of tool arguments (optional)
	"""
	tool_name = params.get("name")
	if not tool_name:
		return _jsonrpc_error(request_id, INVALID_PARAMS, "Missing 'name' in params")

	tool_func = TOOL_REGISTRY.get(tool_name)
	if tool_func is None:
		return _jsonrpc_error(
			request_id,
			METHOD_NOT_FOUND,
			f"Unknown tool: {tool_name}. Available: {', '.join(TOOL_REGISTRY.keys())}",
		)

	arguments = params.get("arguments", {})

	try:
		result = tool_func(**arguments)
		logger.debug("Tool %s executed successfully", tool_name)
		return _jsonrpc_result(request_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
	except TypeError as e:
		return _jsonrpc_error(
			request_id,
			INVALID_PARAMS,
			f"Invalid arguments for tool '{tool_name}': {e}",
		)
	except Exception as e:
		logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
		return _jsonrpc_error(request_id, INTERNAL_ERROR, f"Tool execution failed: {e}")
