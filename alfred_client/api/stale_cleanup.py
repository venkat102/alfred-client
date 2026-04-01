"""Background job for conversation cleanup and maintenance.

Task 6.7: Marks inactive conversations as Stale.
Task 6.4: Cleans up old audit logs based on retention policy.

Register in hooks.py scheduler_events for periodic execution.
"""

import frappe
from frappe.utils import add_to_date, now_datetime


def mark_stale_conversations():
	"""Mark conversations inactive for longer than stale_conversation_hours as Stale.

	Run hourly via scheduler.
	"""
	try:
		settings = frappe.get_single("Alfred Settings")
		stale_hours = settings.stale_conversation_hours or 24
	except Exception:
		stale_hours = 24

	cutoff = add_to_date(now_datetime(), hours=-stale_hours)

	stale_conversations = frappe.get_all(
		"Alfred Conversation",
		filters={
			"status": ["in", ["Open", "In Progress", "Awaiting Input"]],
			"modified": ["<", cutoff],
		},
		pluck="name",
	)

	for conv_name in stale_conversations:
		try:
			conv = frappe.get_doc("Alfred Conversation", conv_name)
			conv.status = "Stale"
			conv.save(ignore_permissions=True)

			frappe.get_doc({
				"doctype": "Alfred Message",
				"conversation": conv_name,
				"role": "system",
				"message_type": "status",
				"content": f"Conversation marked as stale after {stale_hours} hours of inactivity.",
			}).insert(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(f"Failed to mark conversation {conv_name} as stale: {e}")

	if stale_conversations:
		frappe.db.commit()
		frappe.logger().info(f"Marked {len(stale_conversations)} conversations as stale")


def cleanup_old_audit_logs():
	"""Delete audit logs older than the retention period.

	Retention: 90 days (override via site_config key 'audit_log_retention_days').
	Never deletes logs for active or error conversations.
	Run daily via scheduler.
	"""
	retention_days = int(frappe.conf.get("audit_log_retention_days", 90))

	cutoff = add_to_date(now_datetime(), days=-retention_days)

	# Get conversations that should be preserved (active or error)
	preserved = frappe.get_all(
		"Alfred Conversation",
		filters={"status": ["in", ["Open", "In Progress", "Awaiting Input", "Escalated", "Failed"]]},
		pluck="name",
	)

	# Delete old logs not associated with preserved conversations
	if preserved:
		frappe.db.sql("""
			DELETE FROM `tabAlfred Audit Log`
			WHERE creation < %s
			AND conversation NOT IN ({})
		""".format(", ".join(["%s"] * len(preserved))), [cutoff] + preserved)
	else:
		frappe.db.sql("DELETE FROM `tabAlfred Audit Log` WHERE creation < %s", cutoff)

	frappe.db.commit()
	frappe.logger().info("Cleaned up old audit logs (retention: %d days)", retention_days)
