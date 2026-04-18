"""DocType lifecycle hook tests.

Covers the invariants that run implicitly on every save / delete:
- Alfred Conversation.on_trash blocks deletion if audit logs exist
- Alfred Conversation token_usage must be valid JSON
- Alfred Audit Log.validate_immutability raises on re-save
- Alfred Audit Log.on_trash blocks non-Administrator deletion
- Alfred Settings.normalize_llm_model auto-prefixes the default model
- Alfred Settings.normalize_multi_model_names prefixes tier fields
- Alfred Settings.validate_limits guards numeric ranges

Run with:
  bench --site dev.alfred execute alfred_client.test_doctype_hooks.run_tests
"""

from __future__ import annotations

import json

import frappe


def _tolerate_queue_failure(fn):
	"""Swallow the RQ-redis ConnectionError from enqueue_after_commit jobs
	so tests can run without bench services up."""
	try:
		return fn()
	except Exception as e:
		if "Connection refused" in str(e) or "Redis" in str(e):
			frappe.db.commit()
			return None
		raise


def _cleanup(conv_name: str | None) -> None:
	frappe.set_user("Administrator")
	if conv_name:
		frappe.db.delete("Alfred Audit Log", {"conversation": conv_name})
		frappe.db.delete("Alfred Message", {"conversation": conv_name})
		frappe.db.delete("Alfred Changeset", {"conversation": conv_name})
		try:
			frappe.delete_doc("Alfred Conversation", conv_name, force=True, ignore_permissions=True)
		except Exception as e:
			if "Connection refused" in str(e) or "Redis" in str(e):
				frappe.db.delete("Alfred Conversation", {"name": conv_name})
			else:
				raise
	frappe.db.commit()


def run_tests():
	print("\n=== Alfred DocType Hook Tests ===\n")

	conv_name = None
	try:
		# ── Alfred Conversation: on_trash blocks when audit logs exist ────
		print("Test 1: Conversation.on_trash blocks when audit logs exist...")
		conv = frappe.get_doc({
			"doctype": "Alfred Conversation",
			"user": "Administrator",
			"status": "Open",
		})
		conv.insert(ignore_permissions=True)
		frappe.db.commit()
		conv_name = conv.name

		# Insert an audit log for this conversation
		audit = frappe.get_doc({
			"doctype": "Alfred Audit Log",
			"conversation": conv_name,
			"action": "create",
			"document_type": "Server Script",
			"document_name": "test-script",
		})
		audit.insert(ignore_permissions=True)
		frappe.db.commit()

		try:
			frappe.delete_doc("Alfred Conversation", conv_name,
			                 ignore_permissions=True, force=True)
			raise AssertionError("Conversation deletion must be blocked when audit logs exist")
		except AssertionError:
			raise
		except Exception as e:
			msg = str(e).lower()
			assert "audit log" in msg or "immutable" in msg, (
				f"Unexpected error: {e}"
			)
			print(f"  Correctly blocked: {str(e)[:80]}")
		print("  PASSED\n")

		# ── Alfred Conversation: token_usage must be valid JSON ───────────
		print("Test 2: Conversation.token_usage must be valid JSON...")
		conv2 = frappe.get_doc({
			"doctype": "Alfred Conversation",
			"user": "Administrator",
			"status": "Open",
			"token_usage": "{broken json",
		})
		try:
			conv2.insert(ignore_permissions=True)
			raise AssertionError("Invalid JSON in token_usage must be rejected")
		except AssertionError:
			raise
		except Exception as e:
			assert "json" in str(e).lower()
			print(f"  Correctly rejected: {str(e)[:80]}")
		print("  PASSED\n")

		# Valid JSON should pass
		conv3 = frappe.get_doc({
			"doctype": "Alfred Conversation",
			"user": "Administrator",
			"status": "Open",
			"token_usage": json.dumps({"input": 100, "output": 50}),
		})
		conv3.insert(ignore_permissions=True)
		frappe.db.commit()
		extra_conv = conv3.name
		try:
			# ── Alfred Audit Log: validate_immutability ───────────────
			print("Test 3: Audit Log is immutable after creation...")
			log = frappe.get_doc({
				"doctype": "Alfred Audit Log",
				"conversation": extra_conv,
				"action": "create",
				"document_type": "Server Script",
				"document_name": "test-script-2",
			})
			log.insert(ignore_permissions=True)
			frappe.db.commit()

			log.action = "tampered"
			try:
				log.save(ignore_permissions=True)
				raise AssertionError("Audit Log should not be saveable after creation")
			except AssertionError:
				raise
			except Exception as e:
				assert "immutable" in str(e).lower() or "cannot be modified" in str(e).lower()
				print(f"  Correctly blocked: {str(e)[:80]}")
			print("  PASSED\n")

			# ── Alfred Audit Log: on_trash - non-admin blocked ────────
			print("Test 4: Audit Log.on_trash blocks non-Administrator...")
			# Simulate a non-admin context by temporarily setting user
			# (we already know Administrator can delete via cleanup).
			if frappe.db.exists("User", "Guest"):
				frappe.set_user("Guest")
				try:
					frappe.delete_doc("Alfred Audit Log", log.name,
					                 ignore_permissions=True, force=True)
					raise AssertionError("Non-admin deletion of audit log must be blocked")
				except AssertionError:
					raise
				except Exception as e:
					# Any permission error or the specific message is acceptable
					msg = str(e).lower()
					assert any(kw in msg for kw in
					           ("cannot be deleted", "permission", "not permitted"))
					print(f"  Correctly blocked: {str(e)[:80]}")
				finally:
					frappe.set_user("Administrator")
			else:
				print("  Skipped (Guest user not present)")
			print("  PASSED\n")
		finally:
			frappe.db.delete("Alfred Audit Log", {"conversation": extra_conv})
			try:
				frappe.delete_doc("Alfred Conversation", extra_conv,
				                 force=True, ignore_permissions=True)
			except Exception as e:
				if "Connection refused" in str(e) or "Redis" in str(e):
					frappe.db.delete("Alfred Conversation", {"name": extra_conv})
				else:
					raise
			frappe.db.commit()

		# ── Alfred Settings: normalize_llm_model ──────────────────────────
		print("Test 5: Settings.normalize_llm_model auto-prefixes model...")
		settings = frappe.get_single("Alfred Settings")
		orig_model = settings.llm_model
		orig_provider = settings.llm_provider
		try:
			settings.llm_provider = "ollama"
			settings.llm_model = "codegemma:7b"
			settings.normalize_llm_model()
			assert settings.llm_model == "ollama/codegemma:7b", (
				f"Expected 'ollama/codegemma:7b', got {settings.llm_model!r}"
			)
			print(f"  codegemma:7b -> {settings.llm_model}")

			# Pre-prefixed model should be left alone
			settings.llm_model = "openai/gpt-4"
			settings.llm_provider = "openai"
			settings.normalize_llm_model()
			assert settings.llm_model == "openai/gpt-4"
			print(f"  openai/gpt-4 (already prefixed) -> {settings.llm_model}")

			# No provider -> no-op
			settings.llm_provider = ""
			settings.llm_model = "bare-model"
			settings.normalize_llm_model()
			assert settings.llm_model == "bare-model"
			print(f"  no provider: {settings.llm_model} (left alone)")
		finally:
			settings.llm_model = orig_model
			settings.llm_provider = orig_provider
		print("  PASSED\n")

		# ── Alfred Settings: normalize_multi_model_names ──────────────────
		print("Test 6: Settings.normalize_multi_model_names handles tier fields...")
		settings = frappe.get_single("Alfred Settings")
		orig_triage = settings.llm_model_triage
		orig_reasoning = settings.llm_model_reasoning
		orig_agent = settings.llm_model_agent
		orig_provider = settings.llm_provider
		try:
			settings.llm_provider = "ollama"
			settings.llm_model_triage = "gemma:2b"
			settings.llm_model_reasoning = ""
			settings.llm_model_agent = "qwen3:latest"
			settings.normalize_multi_model_names()
			assert settings.llm_model_triage == "ollama/gemma:2b"
			assert settings.llm_model_reasoning == ""  # empty stays empty
			assert settings.llm_model_agent == "ollama/qwen3:latest"
			print(f"  triage: {settings.llm_model_triage}")
			print(f"  reasoning: {settings.llm_model_reasoning!r} (empty preserved)")
			print(f"  agent: {settings.llm_model_agent}")

			# Already prefixed: leave alone
			settings.llm_model_triage = "openai/gpt-3.5-turbo"
			settings.normalize_multi_model_names()
			assert settings.llm_model_triage == "openai/gpt-3.5-turbo"
		finally:
			settings.llm_model_triage = orig_triage
			settings.llm_model_reasoning = orig_reasoning
			settings.llm_model_agent = orig_agent
			settings.llm_provider = orig_provider
		print("  PASSED\n")

		# ── Alfred Settings: validate_limits ──────────────────────────────
		print("Test 7: Settings.validate_limits guards ranges...")
		settings = frappe.get_single("Alfred Settings")
		# Negative max_tokens must throw
		orig_max_tokens = settings.llm_max_tokens
		orig_temp = settings.llm_temperature
		try:
			settings.llm_max_tokens = -10
			try:
				settings.validate_limits()
				raise AssertionError("Negative max_tokens must be rejected")
			except AssertionError:
				raise
			except Exception as e:
				assert "negative" in str(e).lower()
				print(f"  Negative max_tokens blocked: {str(e)[:80]}")

			# Temperature out of range
			settings.llm_max_tokens = 4096
			settings.llm_temperature = 3.0
			try:
				settings.validate_limits()
				raise AssertionError("Temperature > 2 must be rejected")
			except AssertionError:
				raise
			except Exception as e:
				assert "temperature" in str(e).lower() or "between" in str(e).lower()
				print(f"  Temperature 3.0 blocked: {str(e)[:80]}")
		finally:
			settings.llm_max_tokens = orig_max_tokens
			settings.llm_temperature = orig_temp
		print("  PASSED\n")

		print("=== All DocType Hook Tests PASSED ===\n")
	finally:
		_cleanup(conv_name)
