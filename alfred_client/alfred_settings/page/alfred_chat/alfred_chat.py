"""Backend API methods for the Alfred chat page."""

import json

import frappe


@frappe.whitelist()
def get_conversations():
	"""Get the current user's conversations, most recent first."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	conversations = frappe.get_all(
		"Alfred Conversation",
		filters={"user": frappe.session.user},
		fields=["name", "status", "current_agent", "summary", "creation", "modified"],
		order_by="modified desc",
		limit_page_length=50,
	)

	# Use summary field (populated on first message). Fallback to name for legacy records.
	for conv in conversations:
		conv["first_message"] = conv.get("summary") or conv["name"]

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

	# Verify the conversation belongs to the current user
	conv = frappe.get_doc("Alfred Conversation", conversation)
	if conv.user != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(frappe._("You do not have access to this conversation"), frappe.PermissionError)

	# Load latest 200 messages (desc), then reverse to get chronological order.
	# This ensures long conversations show the most recent messages, not the oldest.
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

	# Verify conversation ownership
	conv = frappe.get_doc("Alfred Conversation", conversation)
	if conv.user != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(frappe._("You do not have access to this conversation"), frappe.PermissionError)

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
