"""Resume cursor cache: per-conversation last_msg_id RW.

The connection manager reads ``_load_last_msg_id`` on (re)connect and
sends it into the WS ``resume`` payload so the processing app replays
events the UI missed during the disconnect window. Every user-visible
inbound event calls ``_track_last_msg_id`` (from ``_routing``) so the
cursor advances as events flow through.

Uses ``frappe.cache()`` rather than opening a fresh aioredis client
because every call site is on the sync Frappe side.
"""

from __future__ import annotations

import logging

import frappe

from alfred_client.api.websocket_client._constants import _LAST_MSG_ID_TTL_SECONDS

logger = logging.getLogger("alfred.ws_client")


def _last_msg_id_key(conversation_name: str) -> str:
	return f"alfred:last_msg_id:{conversation_name}"


def _track_last_msg_id(conversation_name: str, msg_id: str) -> None:
	"""Persist the last msg_id the UI received for a conversation.

	Called from ``_route_incoming_message`` on every user-visible event.
	The connection manager reads this back on reconnect and sends it
	into the WS `resume` payload so the server replays events the UI
	missed during the disconnect.

	Uses ``frappe.cache()`` rather than opening a fresh aioredis client
	because this runs on the sync Frappe side (inside a background RQ
	job), not in the async loop. Failures are logged at the call site
	and swallowed - losing one msg_id tick just means the next resume
	replays slightly more than strictly needed.
	"""
	cache = frappe.cache()
	cache.set_value(
		_last_msg_id_key(conversation_name), msg_id,
		expires_in_sec=_LAST_MSG_ID_TTL_SECONDS,
	)


def _load_last_msg_id(conversation_name: str) -> str | None:
	"""Fetch the last msg_id (may be None on first connect or after TTL)."""
	try:
		return frappe.cache().get_value(_last_msg_id_key(conversation_name))
	except Exception as e:
		logger.debug("last_msg_id load failed for %s: %s", conversation_name, e)
		return None
