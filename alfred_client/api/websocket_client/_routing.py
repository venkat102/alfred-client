"""Inbound WS message → browser routing + persistence.

The connection manager's ws_listen task calls ``_route_incoming_message``
on every non-MCP frame. This module owns:
  - the type → realtime-event-name mapping
  - the resume-cursor advance via ``_track_last_msg_id``
  - the volatile run-state cache on Alfred Conversation (so a page
    reload mid-run rehydrates the ticker)
  - the persistence of chat_reply / insights_reply / plan_doc as
    Alfred Message rows
  - the persistence of preview / changeset frames as Alfred Changeset
    rows + the re-publish with the saved doc name
"""

from __future__ import annotations

import json
import logging

import frappe

from alfred_client.api.websocket_client._cache import _track_last_msg_id

logger = logging.getLogger("alfred.ws_client")


# Message types that signal the run is done and the live ticker fields
# on Alfred Conversation should be cleared so a post-run refresh does not
# show a stale "Developer - generating..." label. `preview` / `changeset`
# are included because once the crew has produced a changeset, the ticker
# is no longer meaningful - the user is now reviewing, not waiting.
_RUN_TERMINAL_TYPES = frozenset({
	"error",
	"run_cancelled",
	"chat_reply",
	"insights_reply",
	"plan_doc",
	"preview",
	"changeset",
})


def _route_incoming_message(message, user, conversation_name):
	"""Route an incoming message from the Processing App to the browser.

	Uses frappe.publish_realtime() to forward events to the correct user.
	"""
	msg_type = message.get("type", "")
	data = message.get("data", {})

	if msg_type == "ping":
		return

	logger.info("Routing message: type=%s, user=%s, conversation=%s", msg_type, user, conversation_name)

	event_map = {
		"agent_status": "alfred_agent_status",
		"agent_activity": "alfred_activity",
		"question": "alfred_question",
		"preview": "alfred_preview",
		"changeset": "alfred_preview",
		"error": "alfred_error",
		"echo": "alfred_agent_status",
		"mcp_request": "alfred_mcp_request",
		"deploy": "alfred_deploy",
		"auth_success": None,
		# Three-mode chat (Phase A/B/C): conversational, read-only, and
		# planning handlers stream their output as these new event types.
		# The browser renders them as regular Alfred Message rows, NOT
		# changesets.
		"chat_reply": "alfred_chat_reply",
		"insights_reply": "alfred_insights_reply",
		"plan_doc": "alfred_plan_doc",
		"mode_switch": "alfred_mode_switch",
		# Graceful user-initiated cancel: the processing app emits this when
		# ctx.stop(code="user_cancel") fires, instead of the generic error
		# event. Separate event so the UI can render it as a neutral system
		# message, not an error banner.
		"run_cancelled": "alfred_run_cancelled",
		# Reconnect eviction sentinel: the processing app pushes this onto
		# the conversation's event stream when a new WS connects under the
		# same conversation_id and the prior pipeline is being cancelled
		# (see alfred_processing/alfred/api/websocket/connection.py). The
		# client treats it as a splice point - any in-flight UI state
		# from the cancelled run is stale and the resume-replay path
		# should ignore events before it.
		"run_evicted": "alfred_run_evicted",
		# Non-blocking info notices from the processing app: CLARIFIER_LATE_RESPONSE,
		# MEMORY_SAVE_FAILED, and similar. UI renders as a subtle toast.
		"info": "alfred_info",
	}

	event_name = event_map.get(msg_type)
	if event_name:
		logger.info("Publishing realtime event: %s -> %s", msg_type, event_name)
		# Pass the message envelope's msg_id through to the browser as
		# `_msg_id` on the published data so the client-side dedupe in
		# useAlfredRealtime can drop duplicates (server resume-replay
		# re-sending past a stale last_msg_id cursor; same realtime
		# payload arriving in multiple browser tabs because Frappe
		# realtime is broadcast user-scoped not connection-scoped).
		# Only when data is actually a dict - some processing-side
		# events ship null / scalar payloads, those don't carry the
		# `_msg_id` key but their volume is small enough that any
		# duplicates are harmless.
		envelope_msg_id = message.get("msg_id")
		if envelope_msg_id and isinstance(data, dict):
			# Mutate in place: the dict came from json.loads and is
			# not shared. The downstream resume-tracker (a few lines
			# below) reads `message.get("msg_id")` from the envelope,
			# not from data, so this addition doesn't disturb it.
			data["_msg_id"] = envelope_msg_id
		frappe.publish_realtime(event_name, data, user=user, after_commit=False)

	# Track last_msg_id for resume-on-reconnect. Only stash msg_ids of
	# user-visible events (the same set the server persists to its event
	# stream): skip transport types and the mcp_request sub-protocol.
	# Without this, the connection manager always sends resume with
	# last_msg_id=None and the server treats it as no-op.
	msg_id = message.get("msg_id")
	if msg_id and event_name and msg_type != "mcp_request":
		try:
			_track_last_msg_id(conversation_name, msg_id)
		except Exception as e:
			logger.debug("last_msg_id track failed for %s: %s", conversation_name, e)

	# Cache volatile run state on the conversation so the UI can rehydrate
	# the ticker + phase pipeline on a page refresh. Best-effort; failures
	# here must not affect the realtime delivery above.
	try:
		_update_conversation_run_state(conversation_name, msg_type, data)
	except Exception as e:
		logger.warning(
			"Failed to update run state on Alfred Conversation %s (msg_type=%s): %s",
			conversation_name, msg_type, e,
		)

	# Persist chat / insights replies and plan docs as Alfred Message
	# rows so the conversation scrollback survives page reload. None of
	# them go through the changeset approval flow, so this is the only
	# place they get durably stored.
	if msg_type in ("chat_reply", "insights_reply"):
		try:
			_store_agent_reply_message(conversation_name, msg_type, data)
		except Exception as e:
			logger.warning("Failed to store %s message: %s", msg_type, e)
	elif msg_type == "plan_doc":
		try:
			_store_plan_doc_message(conversation_name, data)
		except Exception as e:
			logger.warning("Failed to store plan_doc message: %s", e)

	# Store changeset previews in the database and notify browser with the doc name
	if msg_type in ("preview", "changeset") and data.get("changes"):
		try:
			# Dry-run result is optional - older processing app versions may not send it.
			# Default to {valid: True, issues: []} so the UI treats legacy changesets as
			# valid (matching the prior behavior where no pre-validation existed).
			dry_run = data.get("dry_run") or {"valid": True, "issues": []}
			dry_run_valid = 1 if dry_run.get("valid") else 0
			dry_run_issues = json.dumps(dry_run.get("issues", []))

			changeset = frappe.get_doc({
				"doctype": "Alfred Changeset",
				"conversation": conversation_name,
				"status": "Pending",
				"changes": json.dumps(data.get("changes", [])),
				"dry_run_valid": dry_run_valid,
				"dry_run_issues": dry_run_issues,
				# Explicit owner - ignore_permissions=True bypasses the default
				# session-user assignment, so set it manually so row-level
				# permissions (the conversation owner should see their own
				# changesets) work correctly.
				"owner": user,
			})
			changeset.insert(ignore_permissions=True)
			frappe.db.commit()
			# Re-publish with the saved changeset name so the UI can fetch it
			frappe.publish_realtime(
				"alfred_preview",
				{"changeset_name": changeset.name, "conversation": conversation_name},
				user=user,
				after_commit=False,
			)
		except Exception as e:
			logger.error("Failed to store changeset: %s", e)


def _update_conversation_run_state(conversation_name, msg_type, data):
	"""Update the refresh-safe run-state fields on an Alfred Conversation.

	- `agent_status` carries the current agent name (and optional pipeline_mode).
	  We cache both so the phase pipeline + "Basic Mode" badge rehydrate on
	  reload.
	- `agent_activity` carries the one-line ticker text. Cache it so the
	  ticker reappears after a refresh mid-run.
	- Any terminal event (see _RUN_TERMINAL_TYPES) clears current_agent and
	  current_activity so the UI does not show stale phase state after the
	  run has ended.

	Best-effort: failures are swallowed by the caller; this is telemetry,
	not critical path.
	"""
	payload = data or {}
	updates: dict[str, object | None] = {}

	if msg_type == "agent_status":
		agent = payload.get("agent")
		if agent:
			updates["current_agent"] = agent
		# The processing app emits pipeline_mode on the first status event
		# of each run. Normalise to the select options on the doctype.
		pipeline_mode = payload.get("pipeline_mode")
		if pipeline_mode:
			normalised = str(pipeline_mode).strip().lower()
			if normalised == "full":
				updates["pipeline_mode"] = "Full"
			elif normalised == "lite":
				updates["pipeline_mode"] = "Lite"
	elif msg_type == "agent_activity":
		message = payload.get("message") or payload.get("text") or payload.get("detail")
		if message:
			updates["current_activity"] = str(message)[:140]
	elif msg_type in _RUN_TERMINAL_TYPES:
		updates["current_agent"] = None
		updates["current_activity"] = None

	if not updates:
		return

	frappe.db.set_value(
		"Alfred Conversation", conversation_name, updates, update_modified=False,
	)


def _store_agent_reply_message(conversation_name, msg_type, data):
	"""Store a chat_reply or insights_reply as an Alfred Message row.

	Three-mode chat (Phase A/B): these messages are conversational / read-only
	and never become changesets, but the user should see them on page reload
	the same way they see dev-mode agent replies.
	"""
	reply_text = (data or {}).get("reply") or ""
	if not reply_text:
		return

	# The current Alfred Message.message_type select enum is
	# text / question / preview / changeset / status / error. Store
	# chat_reply / insights_reply as `text` with a mode marker in
	# metadata so the frontend can distinguish them (and so we don't
	# have to migrate the select field for Phase A/B).
	metadata = {
		"mode": "chat" if msg_type == "chat_reply" else "insights",
		"handler": msg_type,
	}

	msg = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "agent",
		"agent_name": "Alfred" if msg_type == "chat_reply" else "Insights",
		"message_type": "text",
		"content": reply_text,
		"metadata": json.dumps(metadata),
	})
	msg.insert(ignore_permissions=True)
	frappe.db.commit()


def _store_plan_doc_message(conversation_name, data):
	"""Store a plan_doc (Phase C) as an Alfred Message row.

	The plan itself is embedded in metadata.plan so the frontend's
	PlanDocPanel can read it back after a page reload. Content is set to
	the plan title so list views and search still show something useful
	even if the JSON isn't unpacked.
	"""
	plan = (data or {}).get("plan") or {}
	title = plan.get("title") or "Plan"

	metadata = {
		"mode": "plan",
		"handler": "plan_doc",
		"plan": plan,
	}

	msg = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "agent",
		"agent_name": "Planner",
		"message_type": "text",
		"content": title,
		"metadata": json.dumps(metadata),
	})
	msg.insert(ignore_permissions=True)
	frappe.db.commit()
