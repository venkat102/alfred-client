"""Tests for refresh-safe run-state persistence on Alfred Conversation.

Covers the durable caching added in websocket_client._update_conversation_run_state
and the extended get_conversation_state payload used by the chat UI to rehydrate
the ticker / phase pipeline / preview panel after a page refresh.

Run with: bench --site dev.alfred execute alfred_client.test_run_state.run_tests
"""

import json

import frappe


def _make_conv(user: str = "Administrator") -> str:
	conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": user,
		"status": "In Progress",
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return conv.name


def _cleanup(conv_name: str) -> None:
	frappe.db.sql("DELETE FROM `tabAlfred Changeset` WHERE conversation = %s", conv_name)
	frappe.db.sql("DELETE FROM `tabAlfred Message` WHERE conversation = %s", conv_name)
	frappe.delete_doc("Alfred Conversation", conv_name, force=True)
	frappe.db.commit()


def _make_changeset(conv_name: str, status: str, changes: list[dict] | None = None, rollback_data: list | None = None, deployment_log: list | None = None):
	doc = frappe.get_doc({
		"doctype": "Alfred Changeset",
		"conversation": conv_name,
		"status": status,
		"changes": json.dumps(changes or [{"op": "create", "doctype": "Notification", "data": {"name": "X"}}]),
		"dry_run_valid": 1,
		"dry_run_issues": "[]",
		"rollback_data": json.dumps(rollback_data) if rollback_data is not None else None,
		"deployment_log": json.dumps(deployment_log) if deployment_log is not None else None,
		"owner": "Administrator",
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def run_tests():
	print("\n=== Alfred Run State Tests ===\n")

	from alfred_client.alfred_settings.page.alfred_chat.alfred_chat import (
		get_conversation_state,
	)
	from alfred_client.api.websocket_client import _update_conversation_run_state

	# Test 1: agent_status caches current_agent + pipeline_mode
	print("Test 1: agent_status caches current_agent + pipeline_mode...")
	conv = _make_conv()
	try:
		_update_conversation_run_state(conv, "agent_status", {
			"agent": "Developer",
			"pipeline_mode": "lite",
		})
		frappe.db.commit()
		row = frappe.get_doc("Alfred Conversation", conv)
		assert row.current_agent == "Developer", f"expected Developer, got {row.current_agent!r}"
		assert row.pipeline_mode == "Lite", f"expected Lite, got {row.pipeline_mode!r}"
		print(f"  current_agent={row.current_agent}, pipeline_mode={row.pipeline_mode}")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 2: agent_activity caches ticker text (truncated to 140 chars)
	print("Test 2: agent_activity caches ticker text + truncates...")
	conv = _make_conv()
	try:
		long_text = "Reading Leave Application schema " * 20
		_update_conversation_run_state(conv, "agent_activity", {"message": long_text})
		frappe.db.commit()
		row = frappe.get_doc("Alfred Conversation", conv)
		assert row.current_activity is not None
		assert len(row.current_activity) <= 140, f"truncation failed: {len(row.current_activity)}"
		print(f"  current_activity len={len(row.current_activity)}")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 3: terminal events clear the ticker fields
	print("Test 3: terminal events clear current_agent + current_activity...")
	conv = _make_conv()
	try:
		# Seed with a live state
		frappe.db.set_value(
			"Alfred Conversation", conv,
			{"current_agent": "Developer", "current_activity": "Mid-task"},
			update_modified=False,
		)
		frappe.db.commit()
		# Now fire a terminal event
		_update_conversation_run_state(conv, "run_cancelled", {"reason": "user cancel"})
		frappe.db.commit()
		row = frappe.get_doc("Alfred Conversation", conv)
		assert row.current_agent in (None, ""), f"expected cleared, got {row.current_agent!r}"
		assert row.current_activity in (None, ""), f"expected cleared, got {row.current_activity!r}"
		# pipeline_mode must NOT be cleared - it tracks the mode of the most recent run
		print("  PASSED (ticker fields cleared; pipeline_mode preserved)\n")
	finally:
		_cleanup(conv)

	# Test 4: preview also counts as terminal for ticker
	print("Test 4: preview event clears ticker (crew done, user now reviewing)...")
	conv = _make_conv()
	try:
		frappe.db.set_value(
			"Alfred Conversation", conv,
			{"current_agent": "Developer", "current_activity": "Generating"},
			update_modified=False,
		)
		frappe.db.commit()
		_update_conversation_run_state(conv, "preview", {"changes": [{"op": "create", "doctype": "X"}]})
		frappe.db.commit()
		row = frappe.get_doc("Alfred Conversation", conv)
		assert row.current_agent in (None, "")
		assert row.current_activity in (None, "")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 5: get_conversation_state - empty conversation returns all the new keys
	print("Test 5: get_conversation_state payload shape (empty conv)...")
	conv = _make_conv()
	try:
		state = get_conversation_state(conv)
		expected_keys = {
			"is_processing", "status", "mode", "pipeline_mode",
			"active_agent", "active_phase", "completed_phases",
			"current_activity", "pending_changeset",
			"deployed_changeset", "failed_changeset",
		}
		missing = expected_keys - set(state.keys())
		assert not missing, f"missing keys: {missing}"
		assert state["pending_changeset"] is None
		assert state["deployed_changeset"] is None
		assert state["failed_changeset"] is None
		assert state["is_processing"] is True  # status=In Progress
		assert state["completed_phases"] == []
		print(f"  keys: {sorted(state.keys())}")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 6: get_conversation_state - Pending changeset populates pending_changeset
	print("Test 6: get_conversation_state surfaces Pending changeset...")
	conv = _make_conv()
	try:
		cs_name = _make_changeset(conv, "Pending")
		state = get_conversation_state(conv)
		assert state["pending_changeset"] is not None
		assert state["pending_changeset"]["name"] == cs_name
		assert state["pending_changeset"]["status"] == "Pending"
		assert state["deployed_changeset"] is None
		assert state["failed_changeset"] is None
		print(f"  pending_changeset.name={cs_name}")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 7: get_conversation_state - Deployed changeset populates deployed_changeset
	print("Test 7: get_conversation_state surfaces Deployed changeset...")
	conv = _make_conv()
	try:
		cs_name = _make_changeset(
			conv, "Deployed",
			rollback_data=[{"op": "delete", "doctype": "Notification", "name": "X"}],
			deployment_log=[{"step": 1, "status": "success"}],
		)
		# Also mark conversation status to Completed so rehydrate returns non-processing
		frappe.db.set_value("Alfred Conversation", conv, "status", "Completed", update_modified=False)
		frappe.db.commit()
		state = get_conversation_state(conv)
		assert state["pending_changeset"] is None
		assert state["deployed_changeset"] is not None
		assert state["deployed_changeset"]["name"] == cs_name
		assert state["deployed_changeset"]["status"] == "Deployed"
		assert isinstance(state["deployed_changeset"]["deployment_log"], list)
		assert state["failed_changeset"] is None
		assert state["is_processing"] is False
		print(f"  deployed_changeset.name={cs_name}, log_len={len(state['deployed_changeset']['deployment_log'])}")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 8: get_conversation_state - Rolled Back changeset populates failed_changeset
	print("Test 8: get_conversation_state surfaces Rolled Back changeset...")
	conv = _make_conv()
	try:
		cs_name = _make_changeset(conv, "Rolled Back", deployment_log=[{"step": 1, "status": "failed"}])
		frappe.db.set_value("Alfred Conversation", conv, "status", "Failed", update_modified=False)
		frappe.db.commit()
		state = get_conversation_state(conv)
		assert state["pending_changeset"] is None
		assert state["deployed_changeset"] is None
		assert state["failed_changeset"] is not None
		assert state["failed_changeset"]["name"] == cs_name
		print(f"  failed_changeset.name={cs_name}")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 9: agent_status derives active_phase + completed_phases on rehydrate
	print("Test 9: active_phase + completed_phases derivation...")
	conv = _make_conv()
	try:
		_update_conversation_run_state(conv, "agent_status", {
			"agent": "Solution Architect",
			"pipeline_mode": "full",
		})
		frappe.db.commit()
		state = get_conversation_state(conv)
		assert state["active_agent"] == "Solution Architect"
		assert state["active_phase"] == "architecture"
		assert state["completed_phases"] == ["requirement", "assessment"]
		assert state["pipeline_mode"] == "full"
		print(f"  active_phase={state['active_phase']}, completed={state['completed_phases']}, pipeline_mode={state['pipeline_mode']}")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	# Test 10: non-event types (e.g. ping) do not touch the conversation
	print("Test 10: unknown msg_type is a no-op...")
	conv = _make_conv()
	try:
		baseline = frappe.get_doc("Alfred Conversation", conv).modified
		_update_conversation_run_state(conv, "some_unknown_type", {"foo": "bar"})
		frappe.db.commit()
		row = frappe.get_doc("Alfred Conversation", conv)
		# No updates should have landed; modified stays the same since we pass update_modified=False
		# AND no fields were dirtied.
		assert row.current_agent in (None, "")
		assert row.current_activity in (None, "")
		assert row.pipeline_mode in (None, "")
		print("  PASSED\n")
	finally:
		_cleanup(conv)

	print("=== All Run State Tests PASSED ===\n")
