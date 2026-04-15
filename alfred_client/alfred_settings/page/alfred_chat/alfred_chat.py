"""Backend API methods for the Alfred chat page."""

import json

import frappe


@frappe.whitelist()
def get_conversations():
	"""Get conversations the current user owns or that were shared with them."""
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

	for conv in conversations:
		conv["first_message"] = conv.get("summary") or conv["name"]
		conv["is_owner"] = conv["user"] == frappe.session.user

	return conversations


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

	# Check if the RQ background job is running
	from frappe.utils.background_jobs import get_jobs
	site_jobs = get_jobs(site=frappe.local.site, queue="long")
	job_running = False
	for site, jobs in site_jobs.items():
		for job in jobs:
			if isinstance(job, str) and conversation in job:
				job_running = True
				break
			elif isinstance(job, dict) and conversation in str(job.get("job_name", "")):
				job_running = True
				break

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
