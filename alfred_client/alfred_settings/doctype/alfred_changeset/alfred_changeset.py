import json

import frappe
from frappe.model.document import Document


class AlfredChangeset(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		changes: DF.JSON | None
		conversation: DF.Link
		deployment_log: DF.Text | None
		rollback_data: DF.JSON | None
		status: DF.Literal["Pending", "Approved", "Rejected", "Deployed", "Rolled Back"]
	# end: auto-generated types

	def validate(self):
		self.validate_json_fields()

	def validate_json_fields(self):
		"""Validate that JSON fields contain valid JSON."""
		for field in ("changes", "rollback_data"):
			value = getattr(self, field, None)
			if value and isinstance(value, str):
				try:
					json.loads(value)
				except (json.JSONDecodeError, TypeError):
					frappe.throw(frappe._("{0} must be valid JSON").format(field))
