import json

import frappe
from frappe.model.document import Document


class AlfredSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from alfred_client.alfred_settings.doctype.alfred_allowed_role.alfred_allowed_role import (
			AlfredAllowedRole,
		)

		allowed_roles: DF.Table[AlfredAllowedRole]
		api_key: DF.Password | None
		enable_auto_deploy: DF.Check
		llm_api_key: DF.Password | None
		llm_base_url: DF.Data | None
		llm_max_tokens: DF.Int
		llm_model: DF.Data | None
		llm_provider: DF.Literal["", "ollama", "anthropic", "openai", "gemini", "bedrock"]
		llm_temperature: DF.Float
		max_retries_per_agent: DF.Int
		max_tasks_per_user_per_hour: DF.Int
		processing_app_url: DF.Data | None
		redis_url: DF.Data | None
		self_hosted_mode: DF.Check
		stale_conversation_hours: DF.Int
		task_timeout_seconds: DF.Int
		total_conversations: DF.Int
		total_tokens_used: DF.Int
	# end: auto-generated types

	def validate(self):
		self.validate_limits()

	def validate_limits(self):
		if self.llm_max_tokens and self.llm_max_tokens < 0:
			frappe.throw(frappe._("Max Tokens cannot be negative"))

		if self.llm_temperature is not None:
			if self.llm_temperature < 0 or self.llm_temperature > 2:
				frappe.throw(frappe._("Temperature must be between 0.0 and 2.0"))

		if self.task_timeout_seconds and self.task_timeout_seconds < 0:
			frappe.throw(frappe._("Task Timeout cannot be negative"))

		if self.max_retries_per_agent and self.max_retries_per_agent < 0:
			frappe.throw(frappe._("Max Retries Per Agent cannot be negative"))

		if self.stale_conversation_hours and self.stale_conversation_hours < 0:
			frappe.throw(frappe._("Stale Conversation Hours cannot be negative"))
