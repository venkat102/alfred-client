"""Identity helpers: site_id, JWT minting, site_config snapshot.

These are sync-side helpers called both by the connection manager
(during the WS handshake) and by tests (which pin the JWT shape +
site_config keys).
"""

from __future__ import annotations

import time

import jwt as pyjwt

import frappe


def _get_site_id():
	"""Get the canonical site_id used for multi-tenant isolation in the Processing App.

	Returns the bare Frappe site name (e.g. "dev.alfred"), NOT the full URL.
	The Processing App validates this against ^[a-zA-Z0-9._-]+$ because it's used
	as a Redis key namespace - characters like ":" or "/" would either fail
	validation or break key parsing.

	If the Processing App ever needs the full URL (e.g. for Admin Portal callbacks),
	pass it separately via site_config["site_url"], not through site_id.
	"""
	return frappe.local.site


def _generate_jwt(api_key, user=None, roles=None):
	"""Generate a signed JWT for WebSocket handshake."""
	if user is None:
		user = frappe.session.user
	if roles is None:
		roles = frappe.get_roles(user)

	now = int(time.time())
	payload = {
		"user": user,
		"roles": roles,
		"site_id": _get_site_id(),
		"iat": now,
		"exp": now + 86400,
	}
	return pyjwt.encode(payload, api_key, algorithm="HS256")


def _get_site_config():
	"""Read LLM and limit configuration from Alfred Settings."""
	settings = frappe.get_single("Alfred Settings")
	return {
		"site_id": _get_site_id(),
		"llm_provider": settings.llm_provider,
		"llm_model": settings.llm_model,
		"llm_api_key": settings.get_password("llm_api_key") if settings.llm_api_key else "",
		"llm_base_url": settings.llm_base_url or "",
		"llm_max_tokens": settings.llm_max_tokens,
		"llm_temperature": settings.llm_temperature,
		"llm_num_ctx": settings.llm_num_ctx,
		# Per-tier model overrides (empty = use default model)
		"llm_model_triage": settings.llm_model_triage or "",
		"llm_model_triage_num_ctx": settings.llm_model_triage_num_ctx or 0,
		"llm_model_reasoning": settings.llm_model_reasoning or "",
		"llm_model_reasoning_num_ctx": settings.llm_model_reasoning_num_ctx or 0,
		"llm_model_agent": settings.llm_model_agent or "",
		"llm_model_agent_num_ctx": settings.llm_model_agent_num_ctx or 0,
		"pipeline_mode": getattr(settings, "pipeline_mode", None) or "full",
		"max_retries_per_agent": settings.max_retries_per_agent,
		"max_tasks_per_user_per_hour": settings.max_tasks_per_user_per_hour,
		"task_timeout_seconds": settings.task_timeout_seconds,
		# Per-tool-call MCP timeout. The processing app reads this via
		# site_config.get("mcp_timeout", 30) and clamps to 30 when the
		# admin field is unset or zero. Increase when lookup_doctype /
		# run_query routinely take >30s on this site.
		"mcp_timeout": getattr(settings, "mcp_timeout", 0) or 0,
		"enable_auto_deploy": settings.enable_auto_deploy,
	}
