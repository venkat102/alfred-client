import json

import frappe
from frappe.model.document import Document


class AlfredConversation(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from alfred_client.alfred_settings.doctype.alfred_created_document.alfred_created_document import (
			AlfredCreatedDocument,
		)

		created_documents: DF.Table[AlfredCreatedDocument]
		current_agent: DF.Data | None
		escalation_reason: DF.Text | None
		requirement_summary: DF.Text | None
		status: DF.Literal["Open", "In Progress", "Awaiting Input", "Completed", "Escalated", "Failed", "Stale"]
		token_usage: DF.JSON | None
		user: DF.Link
	# end: auto-generated types

	def validate(self):
		self.validate_token_usage_json()

	def validate_token_usage_json(self):
		"""Validate that token_usage contains valid JSON if provided."""
		if self.token_usage:
			try:
				if isinstance(self.token_usage, str):
					json.loads(self.token_usage)
			except (json.JSONDecodeError, TypeError):
				frappe.throw(frappe._("Token Usage must be valid JSON"))

	def on_trash(self):
		"""Block deletion if audit logs exist for this conversation."""
		audit_logs = frappe.db.count("Alfred Audit Log", {"conversation": self.name})
		if audit_logs:
			frappe.throw(
				frappe._("Cannot delete conversation with {0} audit log(s). Audit logs are immutable.").format(
					audit_logs
				)
			)
