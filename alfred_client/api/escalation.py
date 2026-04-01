"""Human escalation flow for Alfred conversations.

When the Assessment Agent determines a task needs human intervention,
or after max retries are exhausted, the conversation is escalated
to a designated developer/admin.
"""

import json

import frappe
from frappe import _


@frappe.whitelist()
def escalate_conversation(conversation_name, reason=""):
	"""Escalate a conversation to a human developer.

	Sets status to 'Escalated', sends notification, and creates
	an escalation entry for the admin dashboard.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	conv = frappe.get_doc("Alfred Conversation", conversation_name)
	conv.status = "Escalated"
	conv.escalation_reason = reason
	conv.save()
	frappe.db.commit()

	# Send notification to System Managers
	_send_escalation_notification(conv, reason)

	# Add system message to conversation
	frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "system",
		"message_type": "status",
		"content": f"Conversation escalated to human developer. Reason: {reason}",
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	# Notify the user via realtime
	frappe.publish_realtime(
		"intern_agent_status",
		{
			"agent": "Orchestrator",
			"status": "escalated",
			"message": f"This request has been escalated to a human developer. Reason: {reason}",
		},
		user=conv.user,
	)

	return {"status": "escalated", "conversation": conversation_name}


@frappe.whitelist()
def take_over_conversation(conversation_name):
	"""Admin takes over an escalated conversation for manual completion."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can take over escalated conversations"))

	conv = frappe.get_doc("Alfred Conversation", conversation_name)
	if conv.status != "Escalated":
		frappe.throw(_("Can only take over escalated conversations"))

	conv.status = "In Progress"
	conv.current_agent = f"Human: {frappe.session.user}"
	conv.save()
	frappe.db.commit()

	frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "system",
		"message_type": "status",
		"content": f"Conversation taken over by {frappe.session.user}",
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "taken_over", "by": frappe.session.user}


@frappe.whitelist()
def return_to_agent(conversation_name):
	"""Return an escalated conversation back to the agent pipeline."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can return conversations to agent"))

	conv = frappe.get_doc("Alfred Conversation", conversation_name)
	conv.status = "In Progress"
	conv.current_agent = None
	conv.escalation_reason = None
	conv.save()
	frappe.db.commit()

	frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "system",
		"message_type": "status",
		"content": f"Conversation returned to AI agent by {frappe.session.user}",
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "returned_to_agent"}


@frappe.whitelist()
def get_escalated_conversations():
	"""Get all escalated conversations for the admin dashboard."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	conversations = frappe.get_all(
		"Alfred Conversation",
		filters={"status": "Escalated"},
		fields=["name", "user", "escalation_reason", "current_agent", "creation", "modified"],
		order_by="modified desc",
	)
	return conversations


def _send_escalation_notification(conv, reason):
	"""Send notification to System Managers about an escalated conversation."""
	system_managers = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		pluck="parent",
	)

	for user in set(system_managers):
		if user == "Administrator":
			continue
		try:
			frappe.publish_realtime(
				"intern_escalation",
				{
					"conversation": conv.name,
					"user": conv.user,
					"reason": reason,
				},
				user=user,
			)
		except Exception:
			pass

	# Also send email notification — escape user-supplied content to prevent HTML injection
	try:
		from frappe.utils import escape_html

		safe_reason = escape_html(reason or "Not specified")
		safe_user = escape_html(conv.user)
		safe_name = escape_html(conv.name)

		frappe.sendmail(
			recipients=list(set(system_managers) - {"Administrator"}),
			subject=f"Alfred Escalation: {safe_name}",
			message=f"""
				<p>An Alfred conversation has been escalated and needs your attention.</p>
				<p><strong>Conversation:</strong> {safe_name}</p>
				<p><strong>User:</strong> {safe_user}</p>
				<p><strong>Reason:</strong> {safe_reason}</p>
				<p><a href="/app/alfred-conversation/{safe_name}">View Conversation</a></p>
			""",
			now=True,
		)
	except Exception:
		pass  # Email failure shouldn't block escalation
