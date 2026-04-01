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
		fields=["name", "status", "current_agent", "creation", "modified"],
		order_by="modified desc",
		limit_page_length=50,
	)
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

	messages = frappe.get_all(
		"Alfred Message",
		filters={"conversation": conversation},
		fields=["name", "role", "agent_name", "content", "message_type", "metadata", "creation"],
		order_by="creation asc",
		limit_page_length=200,
	)
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

	# Update conversation status
	if conv.status == "Open":
		conv.status = "In Progress"
		conv.save()

	frappe.db.commit()

	# Trigger WebSocket send to Processing App (only the send, not duplicate message creation)
	try:
		frappe.enqueue(
			"alfred_client.api.websocket_client._async_send_message",
			conversation_name=conversation,
			msg_type="prompt",
			data={"text": message, "user": frappe.session.user},
			queue="long",
		)
	except Exception:
		pass  # WebSocket may not be connected yet

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
