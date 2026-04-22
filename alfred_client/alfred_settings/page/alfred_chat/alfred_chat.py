"""Backend API methods for the Alfred chat page."""

import json

import frappe


@frappe.whitelist()
def get_conversations():
	"""Get conversations the current user owns or that were shared with them.

	Each row carries enough at-a-glance context for the list UI:
	- `first_message`, `is_owner`: legacy row basics.
	- `message_count`: total Alfred Messages on the conversation.
	- `latest_changeset_state`: slug of the most recent Alfred Changeset
	  status (pending / approved / deploying / rejected / deployed /
	  rolled_back) or "" if no changeset.
	- `latest_changeset_summary`: short human tag derived from the most
	  recent changeset's `changes` JSON (e.g. "DocType: Book", "3 changes").
	- `is_running`: truthy while a pipeline is mid-flight on this
	  conversation. Derived from `current_agent` which the processing
	  app clears on terminal states.

	The message-count and latest-changeset lookups are batched into
	two SQL queries so a 50-row list stays at 3 total DB hits.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	# frappe.get_all respects permission_query_conditions hook,
	# which returns own + shared conversations.
	conversations = frappe.get_all(
		"Alfred Conversation",
		fields=[
			"name", "status", "current_agent", "summary", "user",
			"mode",  # Three-mode chat (Phase D): sticky UI mode preference
			"creation", "modified",
		],
		order_by="modified desc",
		limit_page_length=50,
	)

	names = [c["name"] for c in conversations]
	message_counts = {}
	latest_changesets = {}
	if names:
		count_rows = frappe.db.sql(
			"""
			SELECT conversation, COUNT(*) AS cnt
			FROM `tabAlfred Message`
			WHERE conversation IN %(names)s
			GROUP BY conversation
			""",
			{"names": names},
			as_dict=True,
		)
		message_counts = {r["conversation"]: r["cnt"] for r in count_rows}

		changeset_rows = frappe.db.sql(
			"""
			SELECT cs.conversation, cs.status, cs.changes
			FROM `tabAlfred Changeset` cs
			INNER JOIN (
				SELECT conversation, MAX(creation) AS mc
				FROM `tabAlfred Changeset`
				WHERE conversation IN %(names)s
				GROUP BY conversation
			) latest
				ON cs.conversation = latest.conversation
				AND cs.creation = latest.mc
			""",
			{"names": names},
			as_dict=True,
		)
		latest_changesets = {r["conversation"]: r for r in changeset_rows}

	for conv in conversations:
		conv["first_message"] = conv.get("summary") or conv["name"]
		conv["is_owner"] = conv["user"] == frappe.session.user
		conv["message_count"] = int(message_counts.get(conv["name"], 0))
		conv["is_running"] = bool(conv.get("current_agent"))
		cs = latest_changesets.get(conv["name"])
		if cs:
			conv["latest_changeset_state"] = (cs.get("status") or "").lower().replace(" ", "_")
			conv["latest_changeset_summary"] = _summarise_changeset(cs.get("changes"))
		else:
			conv["latest_changeset_state"] = ""
			conv["latest_changeset_summary"] = ""

	return conversations


def _summarise_changeset(changes_json):
	"""Render a one-line tag like 'DocType: Book' or '3 changes'.

	Parses the Alfred Changeset `changes` field (JSON string of a list
	of {op, doctype, data} items). For a single item, tries
	'doctype: data.name' and falls back to just doctype. For multiple
	items, returns 'N changes'. Returns "" for empty or malformed
	input.
	"""
	if not changes_json:
		return ""
	try:
		items = json.loads(changes_json) if isinstance(changes_json, str) else changes_json
	except (ValueError, TypeError):
		return ""
	if not isinstance(items, list) or not items:
		return ""
	if len(items) == 1:
		item = items[0] or {}
		doctype = item.get("doctype") or ""
		data = item.get("data") or {}
		name = data.get("name") or data.get("role_name") or data.get("fieldname") or ""
		if doctype and name:
			return f"{doctype}: {name}"
		return doctype or "1 change"
	return f"{len(items)} changes"


_VALID_CONVERSATION_MODES = {"Auto", "Dev", "Plan", "Insights"}


@frappe.whitelist()
def set_conversation_mode(conversation, mode):
	"""Persist the user's UI mode preference on the conversation.

	Three-mode chat (Phase D). The frontend calls this whenever the user
	clicks a button in ModeSwitcher. The value is used as the sticky
	default for subsequent prompts on this conversation - the send_message
	`mode` parameter still wins for per-turn overrides.

	Args:
		conversation: Alfred Conversation name.
		mode: One of "Auto" / "Dev" / "Plan" / "Insights". Case-insensitive
			input is normalised to title case. Invalid values raise.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission(
		"Alfred Conversation", ptype="write", doc=conversation, throw=True
	)

	normalised = (mode or "Auto").strip().title()
	if normalised not in _VALID_CONVERSATION_MODES:
		frappe.throw(
			f"Invalid chat mode: {mode!r}. Expected one of "
			f"{sorted(_VALID_CONVERSATION_MODES)}."
		)

	frappe.db.set_value("Alfred Conversation", conversation, "mode", normalised)
	frappe.db.commit()
	return {"name": conversation, "mode": normalised}


@frappe.whitelist()
def create_conversation():
	"""Create a new conversation for the current user."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": frappe.session.user,
		"status": "Open",
	})
	conv.insert()
	frappe.db.commit()
	return {"name": conv.name, "status": conv.status}


@frappe.whitelist()
def get_messages(conversation):
	"""Get messages for a conversation."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission("Alfred Conversation", doc=conversation, throw=True)

	messages = frappe.get_all(
		"Alfred Message",
		filters={"conversation": conversation},
		fields=["name", "role", "agent_name", "content", "message_type", "metadata", "creation"],
		order_by="creation desc",
		limit_page_length=200,
	)
	messages.reverse()
	return messages


_VALID_CHAT_MODES = {"auto", "dev", "plan", "insights"}


@frappe.whitelist()
def send_message(conversation, message, mode="auto"):
	"""Send a user message in a conversation.

	Args:
		conversation: Alfred Conversation name.
		message: User's prompt text.
		mode: Chat mode override from the UI switcher. One of
			"auto" (orchestrator decides), "dev", "plan", "insights".
			Ignored by the processing app when feature flag is off.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission("Alfred Conversation", ptype="write", doc=conversation, throw=True)

	normalized_mode = (mode or "auto").strip().lower()
	if normalized_mode not in _VALID_CHAT_MODES:
		normalized_mode = "auto"

	conv = frappe.get_doc("Alfred Conversation", conversation)

	# Create the message
	msg = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation,
		"role": "user",
		"message_type": "text",
		"content": message,
	})
	msg.insert()

	# Update conversation status and summary
	changed = False
	if conv.status == "Open":
		conv.status = "In Progress"
		changed = True
	if not conv.summary:
		conv.summary = message[:80]
		changed = True
	if changed:
		conv.save()

	frappe.db.commit()

	# Send to Processing App
	# Uses Redis LIST (not pub/sub) so messages are not lost if the connection
	# manager hasn't started yet. The manager drains the list on startup.
	try:
		import uuid as _uuid
		from alfred_client.api.websocket_client import _REDIS_CHANNEL_PREFIX, start_conversation

		# Ensure the connection manager is running for this conversation
		start_conversation(conversation)

		# Push to a Redis list - connection manager will drain this queue
		redis_msg = json.dumps({
			"msg_id": str(_uuid.uuid4()),
			"type": "prompt",
			"data": {
				"text": message,
				"user": frappe.session.user,
				"mode": normalized_mode,
			},
		})
		redis_conn = frappe.cache()
		queue_key = f"{_REDIS_CHANNEL_PREFIX}queue:{conversation}"
		redis_conn.rpush(queue_key, redis_msg)
		# Also publish for immediate delivery if manager is already listening
		channel = f"{_REDIS_CHANNEL_PREFIX}{conversation}"
		redis_conn.publish(channel, redis_msg)
	except Exception as e:
		frappe.logger().error(f"Failed to send to Processing App: {e}")
		# Message is saved in DB even if WS fails - user can retry

	return {"name": msg.name, "status": "sent", "mode": normalized_mode}


@frappe.whitelist()
def delete_conversation(conversation):
	"""Delete a conversation and all its linked records. Only the owner can delete."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	conv = frappe.get_doc("Alfred Conversation", conversation)
	if conv.user != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(frappe._("Only the conversation owner can delete it."), frappe.PermissionError)

	# Delete linked records first
	frappe.db.delete("Alfred Message", {"conversation": conversation})
	frappe.db.delete("Alfred Changeset", {"conversation": conversation})
	frappe.db.delete("Alfred Audit Log", {"conversation": conversation})
	frappe.db.delete("Alfred Created Document", {"parent": conversation})

	# Delete the conversation
	frappe.delete_doc("Alfred Conversation", conversation, force=True)
	frappe.db.commit()

	return {"status": "deleted"}


@frappe.whitelist()
def share_conversation(conversation, user, read=1, write=0):
	"""Share a conversation with another user. Only the owner can share."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	conv = frappe.get_doc("Alfred Conversation", conversation)
	if conv.user != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(frappe._("Only the conversation owner can share it."), frappe.PermissionError)

	frappe.share.add(
		"Alfred Conversation", conversation, user=user,
		read=int(read), write=int(write), notify=1,
	)
	frappe.db.commit()

	return {"status": "shared", "user": user}


@frappe.whitelist()
def get_conversation_health(conversation):
	"""Check the health of a conversation's backend pipeline."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission("Alfred Conversation", doc=conversation, throw=True)

	conv = frappe.get_doc("Alfred Conversation", conversation)
	last_msg = frappe.get_all(
		"Alfred Message",
		filters={"conversation": conversation},
		fields=["creation", "role", "message_type"],
		order_by="creation desc",
		limit_page_length=1,
	)

	# Check if the RQ background job is running. Delegate to the same helper
	# start_conversation uses for its idempotency guard so the two paths can
	# never disagree. The previous inline check used get_jobs() with the
	# default key="method", which returns method path strings - the
	# conversation name is never embedded there, so the substring test
	# always matched nothing and background_job_running was permanently
	# False even with live managers.
	from alfred_client.api.websocket_client import (
		_conversation_job_in_flight,
		_long_queue_worker_count,
	)
	job_running = _conversation_job_in_flight(conversation)

	# How many workers are actually serving the 'long' queue. If this is
	# zero, no connection manager can run until the operator starts
	# worker_long (Procfile + bench restart). This is usually the real
	# root cause when background_job_running is false with queued items
	# pending - surface it first so the health toast points at the fix.
	long_worker_count = _long_queue_worker_count()

	# Check Redis queue depth
	redis_conn = frappe.cache()
	queue_key = f"alfred:ws:outbound:queue:{conversation}"
	queue_depth = redis_conn.llen(queue_key) or 0

	# Check Processing App reachability
	processing_app_ok = False
	processing_app_error = ""
	try:
		settings = frappe.get_single("Alfred Settings")
		if settings.processing_app_url:
			import requests
			url = settings.processing_app_url.rstrip("/")
			# Try a simple HTTP health check (most WS servers expose this)
			http_url = url.replace("ws://", "http://").replace("wss://", "https://")
			resp = requests.get(f"{http_url}/health", timeout=5)
			processing_app_ok = resp.status_code == 200
		else:
			processing_app_error = "processing_app_url not configured"
	except Exception as e:
		processing_app_error = str(e)

	return {
		"conversation_status": conv.status,
		"current_agent": conv.current_agent,
		"last_message": last_msg[0] if last_msg else None,
		"background_job_running": job_running,
		"long_worker_count": long_worker_count,
		"redis_queue_depth": queue_depth,
		"processing_app_reachable": processing_app_ok,
		"processing_app_error": processing_app_error,
	}


@frappe.whitelist()
def approve_changeset(changeset_name):
	"""Approve a changeset - runs dry-run validation first, then deploys.

	Caller must have write permission on the changeset's parent conversation
	(owner, shared-with-write, or System Manager). Otherwise any user with
	the Alfred role could approve any other user's pending changeset.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	# Route through the `changeset_has_permission` hook which delegates to
	# the parent conversation's owner/share/System-Manager check.
	frappe.has_permission(
		"Alfred Changeset", ptype="write", doc=changeset_name, throw=True,
	)

	cs = frappe.get_doc("Alfred Changeset", changeset_name)
	if cs.status != "Pending":
		frappe.throw(
			frappe._("Changeset is not in Pending status (current: {0})").format(cs.status)
		)

	# Parse stored changes, handling all the shapes the DB could hold:
	# - str → JSON-decode
	# - list → use as-is
	# - None / empty → fail clearly instead of silently deploying nothing
	try:
		if isinstance(cs.changes, str):
			changes = json.loads(cs.changes)
		elif isinstance(cs.changes, list):
			changes = cs.changes
		else:
			changes = []
	except json.JSONDecodeError as e:
		frappe.logger().error(
			f"approve_changeset: failed to parse changes for {changeset_name}: {e}"
		)
		frappe.throw(
			frappe._("Changeset data is corrupted and cannot be parsed: {0}").format(e)
		)

	if not changes:
		frappe.throw(frappe._("Changeset is empty - nothing to deploy."))

	frappe.logger().info(
		f"approve_changeset: deploying {changeset_name} with {len(changes)} operation(s) "
		f"for user {frappe.session.user}"
	)

	# Dry-run validation - test all operations without committing.
	# This runs a SECOND time at approve (first was pre-preview) as a safety net
	# against DB state drift between preview and deploy.
	from alfred_client.api.deploy import dry_run_changeset
	dry_run = dry_run_changeset(changes)

	# Log any disagreement between the pre-preview dry-run and this second pass.
	# If preview-time was invalid but approve-time is now valid, something changed
	# in the DB (e.g., a conflicting row was deleted). If preview was valid but
	# approve is now invalid, something was added. Either is worth alerting on.
	preview_valid = int(cs.dry_run_valid or 0)
	approve_valid = 1 if dry_run["valid"] else 0
	if preview_valid != approve_valid:
		frappe.logger().warning(
			f"Dry-run disagreement for changeset {changeset_name}: "
			f"preview-time valid={preview_valid}, approve-time valid={approve_valid}. "
			f"Database state likely drifted between preview and approve."
		)

	if not dry_run["valid"]:
		# Return validation errors without deploying
		critical_issues = [i for i in dry_run["issues"] if i["severity"] == "critical"]
		return {
			"status": "validation_failed",
			"message": f"Dry-run failed with {len(critical_issues)} critical issue(s). Deployment aborted.",
			"issues": dry_run["issues"],
			"validated": dry_run["validated"],
		}

	cs.status = "Approved"
	cs.save()
	frappe.db.commit()

	# Deploy (dry-run passed)
	from alfred_client.api.deploy import apply_changeset
	result = apply_changeset(changeset_name)

	# Include dry-run results in the response
	result["dry_run"] = dry_run

	return result


@frappe.whitelist()
def reject_changeset(changeset_name):
	"""Reject a changeset. Caller must have write perm on the changeset."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission(
		"Alfred Changeset", ptype="write", doc=changeset_name, throw=True,
	)

	cs = frappe.get_doc("Alfred Changeset", changeset_name)
	cs.status = "Rejected"
	cs.save()
	frappe.db.commit()

	return {"status": "rejected"}


@frappe.whitelist()
def get_changeset(changeset_name):
	"""Get changeset details for preview. Read permission required."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission(
		"Alfred Changeset", ptype="read", doc=changeset_name, throw=True,
	)

	cs = frappe.get_doc("Alfred Changeset", changeset_name)
	changes = json.loads(cs.changes) if cs.changes else []
	dry_run_issues = []
	if cs.dry_run_issues:
		try:
			dry_run_issues = json.loads(cs.dry_run_issues)
		except json.JSONDecodeError:
			dry_run_issues = []

	return {
		"name": cs.name,
		"status": cs.status,
		"conversation": cs.conversation,
		"changes": changes,
		"deployment_log": cs.deployment_log,
		"dry_run_valid": int(cs.dry_run_valid or 0),
		"dry_run_issues": dry_run_issues,
		"creation": str(cs.creation) if cs.creation else None,
	}


@frappe.whitelist()
def get_latest_changeset(conversation):
	"""Get the most recent changeset for a conversation (polling fallback).

	Used when realtime events don't arrive - the UI polls this endpoint
	every few seconds while processing to detect when a changeset is ready.
	Caller must have read permission on the parent conversation.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	# Gate on the PARENT conversation, not the changeset - at poll start there
	# may be no changeset yet and we don't want to leak a "no changeset"
	# vs "exists but you can't see it" side channel.
	frappe.has_permission(
		"Alfred Conversation", ptype="read", doc=conversation, throw=True,
	)

	cs_name = frappe.db.get_value(
		"Alfred Changeset",
		{"conversation": conversation},
		"name",
		order_by="creation desc",
	)
	if not cs_name:
		return None

	cs = frappe.get_doc("Alfred Changeset", cs_name)
	changes = json.loads(cs.changes) if cs.changes else []
	dry_run_issues = []
	if cs.dry_run_issues:
		try:
			dry_run_issues = json.loads(cs.dry_run_issues)
		except json.JSONDecodeError:
			dry_run_issues = []

	return {
		"name": cs.name,
		"status": cs.status,
		"conversation": cs.conversation,
		"changes": changes,
		"deployment_log": cs.deployment_log,
		"dry_run_valid": int(cs.dry_run_valid or 0),
		"dry_run_issues": dry_run_issues,
		# Creation timestamp lets the UI's polling fallback reject stale
		# changesets from previous prompts in the same conversation.
		"creation": str(cs.creation) if cs.creation else None,
	}


# Canonical phase order, mirroring the frontend's AGENT_PHASE_MAP.
# Used to derive "completed phases" from the conversation's recorded
# current_agent on reload: anything earlier than the active agent is
# treated as done. Keep in sync with AlfredChatApp.vue's AGENT_PHASE_MAP.
_AGENT_TO_PHASE = [
	("Requirement Analyst", "requirement"),
	("Feasibility Assessor", "assessment"),
	("Solution Architect", "architecture"),
	("Developer", "development"),
	("QA Validator", "testing"),
	("Deployment Specialist", "deployment"),
]
_PROCESSING_STATUSES = {"In Progress", "Awaiting Input"}


@frappe.whitelist()
def get_conversation_state(conversation):
	"""Return the live state of a conversation for UI rehydration on reload.

	The chat UI resets its in-memory state (preview panel, active phase,
	processing flag) on every openConversation, then listens for future
	realtime events. That works great for normal navigation but loses
	context on a mid-run refresh - the pipeline is still running on the
	processing app, and a pending changeset may already be in the DB, but
	the UI has no handle to either. This endpoint gives the UI a snapshot
	so it can re-attach to "where we were" before subscribing to new events.

	Returns:
	  {
	    "is_processing": bool,           # true if pipeline is mid-run
	    "status": str,                   # conversation status string
	    "mode": str | None,              # sticky chat mode (auto/dev/plan/insights)
	    "pipeline_mode": str | None,     # "full" | "lite" (from last run)
	    "active_agent": str | None,      # e.g. "Developer"
	    "active_phase": str | None,      # e.g. "development"
	    "completed_phases": list[str],   # phases derived as done on reload
	    "current_activity": str | None,  # live ticker text if run in flight
	    "pending_changeset": dict|None,  # latest Pending changeset if any
	    "deployed_changeset": dict|None, # latest Deployed changeset if any
	    "failed_changeset": dict|None,   # latest Rolled Back / Failed changeset if any
	  }

	At most one of pending / deployed / failed is expected to be relevant
	at a given moment but all three are returned so the UI can resolve
	races (e.g. deploy just completed, status transitioning). The UI picks
	the variant to show based on a precedence: pending > deployed > failed.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission("Alfred Conversation", doc=conversation, throw=True)

	conv = frappe.get_doc("Alfred Conversation", conversation)
	status = conv.status or "Open"
	is_processing = status in _PROCESSING_STATUSES

	active_agent = conv.current_agent or None
	active_phase = None
	completed_phases = []
	if active_agent:
		for agent, phase in _AGENT_TO_PHASE:
			if agent == active_agent:
				active_phase = phase
				break
			completed_phases.append(phase)

	return {
		"is_processing": is_processing,
		"status": status,
		"mode": (conv.mode or "").lower() or None,
		"pipeline_mode": (conv.pipeline_mode or "").lower() or None,
		"active_agent": active_agent,
		"active_phase": active_phase,
		"completed_phases": completed_phases,
		"current_activity": conv.current_activity or None,
		"pending_changeset": _fetch_changeset_by_status(conversation, ("Pending",)),
		"deployed_changeset": _fetch_changeset_by_status(conversation, ("Deployed",)),
		"failed_changeset": _fetch_changeset_by_status(conversation, ("Rolled Back",)),
	}


def _fetch_changeset_by_status(conversation, statuses):
	"""Return the latest Alfred Changeset with status in `statuses`, shaped
	for the preview panel, or None if none exists.

	Shared by get_conversation_state for the pending / deployed / failed
	variants so we do not repeat the same load-and-shape logic three times.
	"""
	name = frappe.db.get_value(
		"Alfred Changeset",
		{"conversation": conversation, "status": ["in", list(statuses)]},
		"name",
		order_by="creation desc",
	)
	if not name:
		return None
	cs = frappe.get_doc("Alfred Changeset", name)
	try:
		changes = json.loads(cs.changes) if cs.changes else []
	except json.JSONDecodeError:
		changes = []
	try:
		dry_run_issues = json.loads(cs.dry_run_issues) if cs.dry_run_issues else []
	except json.JSONDecodeError:
		dry_run_issues = []
	deployment_log = None
	if cs.deployment_log:
		try:
			deployment_log = json.loads(cs.deployment_log)
		except json.JSONDecodeError:
			deployment_log = cs.deployment_log
	return {
		"name": cs.name,
		"status": cs.status,
		"conversation": cs.conversation,
		"changes": changes,
		"deployment_log": deployment_log,
		"dry_run_valid": int(cs.dry_run_valid or 0),
		"dry_run_issues": dry_run_issues,
		"creation": str(cs.creation) if cs.creation else None,
	}
