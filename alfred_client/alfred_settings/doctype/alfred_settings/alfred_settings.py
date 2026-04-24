import json
from urllib.parse import urlparse

import frappe
import requests
from frappe.model.document import Document

# Hostnames treated as loopback. Plaintext HTTP/WS is accepted only for
# these because the traffic never leaves the host. Anything else (private
# networks, public domains) must use HTTPS/WSS so the handshake-carried
# llm_api_key doesn't ride the wire in cleartext.
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def _check_processing_app_url(raw: str) -> str | None:
	"""Validate a processing_app_url string.

	Returns None if the URL is acceptable (https/wss, or plaintext
	against a loopback host). Returns an error message string otherwise.
	Empty / None inputs are treated as "not yet set" and accepted; the
	field is optional at save time.

	Pure function - no Frappe calls - so unit tests can exercise it
	outside a bench context. The instance method ``validate_processing_app_url``
	wraps this and calls ``frappe.throw()`` on rejection.
	"""
	if not raw or not raw.strip():
		return None

	parsed = urlparse(raw.strip())
	scheme = (parsed.scheme or "").lower()
	host = (parsed.hostname or "").lower()

	if scheme in {"https", "wss"}:
		return None
	if scheme in {"http", "ws"}:
		if host in _LOOPBACK_HOSTS or host.startswith("127."):
			return None
		port_path = (
			f":{parsed.port}{parsed.path}" if parsed.port else parsed.path
		)
		return (
			"Processing App URL must use https:// or wss:// when pointing "
			"to a non-loopback host. The WebSocket handshake carries "
			"llm_api_key in site_config; over plaintext http:// / ws:// a "
			"network attacker can sniff it. "
			f"Fix: change the URL to https://{host or 'your-host'}{port_path} "
			"(or terminate TLS at a reverse proxy in front of the processing "
			"app). Loopback addresses like http://localhost:8001 are still "
			"allowed for local dev."
		)
	return (
		f"Processing App URL has an unsupported scheme {scheme or '<missing>'!r}. "
		"Expected one of: https, wss, http (loopback only), ws (loopback only)."
	)


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
		llm_model_agent: DF.Data | None
		llm_model_agent_num_ctx: DF.Int
		llm_model_reasoning: DF.Data | None
		llm_model_reasoning_num_ctx: DF.Int
		llm_model_triage: DF.Data | None
		llm_model_triage_num_ctx: DF.Int
		llm_num_ctx: DF.Int
		llm_provider: DF.Literal["", "ollama", "anthropic", "openai", "gemini", "bedrock"]
		llm_temperature: DF.Float
		max_retries_per_agent: DF.Int
		max_tasks_per_user_per_hour: DF.Int
		mcp_timeout: DF.Int
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
		self.validate_processing_app_url()
		self.normalize_llm_model()
		self.normalize_multi_model_names()

	def validate_processing_app_url(self):
		"""Reject plaintext schemes unless the target is loopback.

		The WebSocket handshake carries llm_api_key inside site_config.
		If the URL is http:// or ws:// and points to a non-loopback host,
		that secret rides the wire in cleartext on every connection. We
		hard-fail the save in that case with an actionable message. Users
		running the processing app on the same machine (the common dev
		case) can still use plain http:// / ws:// against localhost /
		127.0.0.1 / ::1.

		Logic is in :func:`_check_processing_app_url` so it's
		bench-independent and unit-testable.
		"""
		error = _check_processing_app_url(self.processing_app_url or "")
		if error:
			frappe.throw(frappe._(error))

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

	def normalize_llm_model(self):
		"""Auto-prefix model name with provider if missing.

		Users often type 'codegemma:7b' instead of 'ollama/codegemma:7b'.
		LiteLLM requires the provider prefix to route correctly.
		"""
		if not self.llm_model or not self.llm_provider:
			return

		model = self.llm_model.strip()
		provider = self.llm_provider.strip()

		# If model already has a provider prefix (contains /), leave it
		if "/" in model:
			return

		# Auto-prefix: codegemma:7b -> ollama/codegemma:7b
		self.llm_model = f"{provider}/{model}"

	def normalize_multi_model_names(self):
		"""Auto-prefix per-tier model names with provider, same as normalize_llm_model."""
		if not self.llm_provider:
			return
		provider = self.llm_provider.strip()
		for field in ("llm_model_triage", "llm_model_reasoning", "llm_model_agent"):
			val = (getattr(self, field, None) or "").strip()
			if val and "/" not in val:
				setattr(self, field, f"{provider}/{val}")


@frappe.whitelist()
def test_llm_connection():
	"""Test connectivity to the configured LLM endpoint.

	For Ollama: calls /api/tags to list available models.
	For cloud providers: attempts a minimal completion call.

	Returns: {"status": "ok|error", "message": str, "models": list|None}
	"""
	settings = frappe.get_single("Alfred Settings")
	provider = settings.llm_provider
	base_url = settings.llm_base_url or ""
	model = settings.llm_model or ""
	api_key = settings.get_password("llm_api_key") if settings.llm_api_key else ""

	if not provider:
		return {"status": "error", "message": "No LLM provider configured. Set it in the LLM Configuration tab."}

	try:
		if provider == "ollama":
			return _test_ollama(base_url, model)
		else:
			return _test_cloud_provider(provider, model, api_key, base_url)
	except Exception as e:
		return {"status": "error", "message": str(e)}


def _test_ollama(base_url, model):
	"""Test Ollama connection - list models and optionally test generation."""
	if not base_url:
		return {"status": "error", "message": "LLM Base URL is required for Ollama. Set it to http://your-server:11434"}

	url = base_url.rstrip("/")

	# Step 1: Check if Ollama is reachable
	try:
		resp = requests.get(f"{url}/api/tags", timeout=10)
		resp.raise_for_status()
		data = resp.json()
	except requests.ConnectionError:
		return {"status": "error", "message": f"Cannot connect to Ollama at {url}. Check the URL and ensure Ollama is running."}
	except requests.Timeout:
		return {"status": "error", "message": f"Connection to {url} timed out. The server may be slow or unreachable."}
	except Exception as e:
		return {"status": "error", "message": f"Error connecting to Ollama: {e}"}

	# Step 2: List available models
	available_models = [m.get("name", "") for m in data.get("models", [])]

	# Step 3: Check if the configured model is available
	# Extract model name without provider prefix (ollama/codegemma:7b → codegemma:7b)
	short_model = model.split("/", 1)[-1] if "/" in model else model
	model_found = any(short_model in m for m in available_models)

	if not available_models:
		return {
			"status": "warning",
			"message": f"Connected to Ollama at {url}, but no models are installed. Run: ollama pull {short_model}",
			"models": [],
		}

	if not model_found and short_model:
		return {
			"status": "warning",
			"message": f"Connected to Ollama at {url}. Model '{short_model}' not found. Available models: {', '.join(available_models)}. Run: ollama pull {short_model}",
			"models": available_models,
		}

	# Step 4: Quick generation test
	try:
		test_resp = requests.post(
			f"{url}/api/generate",
			json={"model": short_model, "prompt": "Hi", "stream": False},
			timeout=30,
		)
		test_resp.raise_for_status()
		return {
			"status": "ok",
			"message": f"Connected to Ollama at {url}. Model '{short_model}' is responding. Available models: {', '.join(available_models)}",
			"models": available_models,
		}
	except requests.Timeout:
		return {
			"status": "warning",
			"message": f"Connected to Ollama at {url}. Model '{short_model}' found but generation timed out (30s). The model may be loading for the first time - try again.",
			"models": available_models,
		}
	except Exception as e:
		return {
			"status": "warning",
			"message": f"Connected to Ollama at {url}. Model '{short_model}' found but generation test failed: {e}",
			"models": available_models,
		}


@frappe.whitelist()
def check_processing_app():
	"""Test connectivity to the Processing App's /health endpoint."""
	settings = frappe.get_single("Alfred Settings")
	url = settings.processing_app_url
	if not url:
		return {"reachable": False, "error": "Processing App URL is not configured."}

	try:
		resp = requests.get(f"{url.rstrip('/')}/health", timeout=10)
		resp.raise_for_status()
		data = resp.json()
		return {"reachable": True, "version": data.get("version"), "redis": data.get("redis")}
	except requests.ConnectionError:
		return {"reachable": False, "error": f"Cannot connect to {url}"}
	except requests.Timeout:
		return {"reachable": False, "error": f"Connection to {url} timed out"}
	except Exception as e:
		return {"reachable": False, "error": str(e)}


def _test_cloud_provider(provider, model, api_key, base_url):
	"""Test cloud LLM provider connection with a minimal API call."""
	if not api_key and provider != "bedrock":
		return {"status": "error", "message": f"API key is required for {provider}. Set it in the LLM Configuration tab."}

	if not model:
		return {"status": "error", "message": "No model configured. Set the LLM Model field."}

	# Use LiteLLM for the test call
	try:
		import litellm

		kwargs = {"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5}
		if api_key:
			kwargs["api_key"] = api_key
		if base_url:
			kwargs["base_url"] = base_url

		response = litellm.completion(**kwargs)
		return {"status": "ok", "message": f"Connected to {provider}. Model '{model}' is responding."}
	except ImportError:
		return {"status": "error", "message": "LiteLLM is not installed in the Frappe environment. The Processing App handles LLM calls - test from there instead."}
	except Exception as e:
		return {"status": "error", "message": f"Connection to {provider} failed: {e}"}
