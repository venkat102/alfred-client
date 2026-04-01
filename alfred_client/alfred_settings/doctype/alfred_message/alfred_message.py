import json

import frappe
from frappe.model.document import Document


class AlfredMessage(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agent_name: DF.Data | None
		content: DF.TextEditor | None
		conversation: DF.Link
		message_type: DF.Literal["text", "question", "preview", "changeset", "status", "error"]
		metadata: DF.JSON | None
		role: DF.Literal["user", "agent", "system"]
	# end: auto-generated types

	def validate(self):
		if self.metadata:
			try:
				if isinstance(self.metadata, str):
					json.loads(self.metadata)
			except (json.JSONDecodeError, TypeError):
				frappe.throw(frappe._("Metadata must be valid JSON"))
