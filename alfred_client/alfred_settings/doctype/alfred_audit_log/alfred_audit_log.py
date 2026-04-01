import json

import frappe
from frappe.model.document import Document


class AlfredAuditLog(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		action: DF.Data
		after_state: DF.JSON | None
		agent: DF.Data | None
		before_state: DF.JSON | None
		conversation: DF.Link
		document_name: DF.Data | None
		document_type: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self.validate_json_fields()
		self.validate_immutability()

	def validate_json_fields(self):
		for field in ("before_state", "after_state"):
			value = getattr(self, field, None)
			if value and isinstance(value, str):
				try:
					json.loads(value)
				except (json.JSONDecodeError, TypeError):
					frappe.throw(frappe._("{0} must be valid JSON").format(field))

	def validate_immutability(self):
		"""Prevent modification of existing audit log records."""
		if not self.is_new():
			frappe.throw(
				frappe._("Audit log records are immutable and cannot be modified after creation."),
				frappe.PermissionError,
			)

	def on_trash(self):
		"""Prevent deletion of audit log records."""
		if frappe.session.user != "Administrator":
			frappe.throw(
				frappe._("Audit log records cannot be deleted."),
				frappe.PermissionError,
			)
