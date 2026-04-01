import frappe
from frappe.model.document import Document


class AlfredCreatedDocument(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		document_name: DF.Data | None
		document_type: DF.Data | None
		operation: DF.Literal["Created", "Modified", "Deleted"]
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
	# end: auto-generated types

	pass
