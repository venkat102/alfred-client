"""MCP-over-WebSocket transport adapter.

Distinguishes MCP (JSON-RPC 2.0) messages from custom messages on the
same WebSocket connection. MCP messages have a "jsonrpc" field.

This module is used by the WebSocket client (api/websocket_client.py) to
route incoming messages to the MCP server when they're MCP requests.
"""

import json
import logging
from typing import Any

from alfred_client.mcp.server import handle_mcp_request

logger = logging.getLogger("alfred.mcp.transport")


def is_mcp_message(data: dict) -> bool:
	"""Check if a message is an MCP (JSON-RPC 2.0) message.

	MCP messages have a "jsonrpc" field set to "2.0".
	Custom messages have a "type" field instead.
	"""
	return "jsonrpc" in data


def process_mcp_message(raw_message: str | dict) -> dict:
	"""Process an MCP message and return the JSON-RPC response.

	Args:
		raw_message: The incoming message (string or dict).

	Returns:
		JSON-RPC 2.0 response dict to send back.
	"""
	return handle_mcp_request(raw_message)


def route_websocket_message(raw_message: str) -> tuple[str, dict]:
	"""Route a WebSocket message to the appropriate handler.

	Returns:
		Tuple of (message_type, response_or_data):
		- ("mcp", response_dict) for MCP messages
		- ("custom", parsed_data) for custom messages
		- ("error", error_dict) for parse errors
	"""
	try:
		if isinstance(raw_message, str):
			data = json.loads(raw_message)
		else:
			data = raw_message
	except json.JSONDecodeError as e:
		return ("error", {"error": f"Invalid JSON: {e}"})

	if is_mcp_message(data):
		response = process_mcp_message(data)
		return ("mcp", response)
	else:
		return ("custom", data)
