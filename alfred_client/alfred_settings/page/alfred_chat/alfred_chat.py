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
		fields=["name", "status", "current_agent", "summary", "user", "creation", "modified"],
		order_by="modified desc",
		limit_page_length=50,
	)

	for conv in conversations:
		conv["first_message"] = conv.get("summary") or conv["name"]
		conv["is_owner"] = conv["user"] == frappe.session.user

	return conversations


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


@frappe.whitelist()
def send_message(conversation, message):
	"""Send a user message in a conversation."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()
	frappe.has_permission("Alfred Conversation", ptype="write", doc=conversation, throw=True)

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
			"data": {"text": message, "user": frappe.session.user},
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

	return {"name": msg.name, "status": "sent"}


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
	"""Approve a changeset for deployment."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	cs = frappe.get_doc("Alfred Changeset", changeset_name)
	if cs.status != "Pending":
		frappe.throw(frappe._("Changeset is not in Pending status"))

	cs.status = "Approved"
	cs.save()
	frappe.db.commit()

	# Trigger deployment
	from alfred_client.api.deploy import apply_changeset
	result = apply_changeset(changeset_name)

	return result


@frappe.whitelist()
def reject_changeset(changeset_name):
	"""Reject a changeset."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	cs = frappe.get_doc("Alfred Changeset", changeset_name)
	cs.status = "Rejected"
	cs.save()
	frappe.db.commit()

	return {"status": "rejected"}


@frappe.whitelist()
def get_changeset(changeset_name):
	"""Get changeset details for preview."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	cs = frappe.get_doc("Alfred Changeset", changeset_name)
	changes = json.loads(cs.changes) if cs.changes else []

	return {
		"name": cs.name,
		"status": cs.status,
		"conversation": cs.conversation,
		"changes": changes,
		"deployment_log": cs.deployment_log,
	}
