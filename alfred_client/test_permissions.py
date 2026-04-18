"""Cross-user / cross-ownership permission enforcement tests.

These tests verify the six-layer permission model actually holds under
adversarial conditions: user B must NOT be able to read, write, approve,
deploy, rollback, delete, or share user A's conversation resources via
any whitelisted endpoint.

Strategy
--------
- Provisioned two disposable test users (alice and bob) with the role
  "Alfred Tester" added to Alfred Settings.allowed_roles so they pass
  the role gate but are NOT System Managers (SM bypasses every owner
  check by design).
- Each scenario flips frappe.session.user between the two and invokes
  the real whitelisted API. Every negative case must raise
  frappe.PermissionError.
- Teardown: roll back to Administrator and delete the fixture users +
  conversation trail. If a test crashes mid-run, the cleanup still runs
  under the outer try/finally.

Run with:
  bench --site dev.alfred execute alfred_client.test_permissions.run_tests
"""

from __future__ import annotations

import frappe

_ALICE = "alice_alfred_test@example.com"
_BOB = "bob_alfred_test@example.com"
_TEST_ROLE = "Alfred Tester"


def _ensure_role(name: str) -> None:
	if not frappe.db.exists("Role", name):
		role = frappe.get_doc({
			"doctype": "Role",
			"role_name": name,
			"desk_access": 1,
		})
		role.insert(ignore_permissions=True)


def _ensure_role_doctype_perm(role: str, doctype: str) -> None:
	"""Grant full CRUD to `role` on `doctype` via Custom DocPerm if absent.

	The role-level permission is what Frappe checks when a user runs
	`doc.insert()` / `doc.save()` / `doc.delete()`. The *per-doc* hooks
	(conversation_has_permission etc.) are layered ON TOP of this - so
	without the role-level grant every endpoint call falls at the first
	gate and we never reach the owner check we actually want to test.
	"""
	existing = frappe.db.exists(
		"Custom DocPerm",
		{"parent": doctype, "role": role, "permlevel": 0},
	)
	if existing:
		return
	perm = frappe.get_doc({
		"doctype": "Custom DocPerm",
		"parent": doctype,
		"parenttype": "DocType",
		"parentfield": "permissions",
		"role": role,
		"permlevel": 0,
		"read": 1, "write": 1, "create": 1, "delete": 1,
		"share": 1, "export": 1, "print": 1, "email": 1, "report": 1,
	})
	perm.insert(ignore_permissions=True)
	frappe.clear_cache(doctype=doctype)


def _ensure_user(email: str) -> None:
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"enabled": 1,
			"send_welcome_email": 0,
			"user_type": "System User",
		})
		# User.on_update enqueues create_contact via RQ; if bench's RQ redis
		# isn't up the insert explodes even though the row is written.
		# Swallow the enqueue failure when the user lands in the DB anyway.
		try:
			user.insert(ignore_permissions=True)
		except Exception as e:
			if "Connection refused" in str(e) and frappe.db.exists("User", email):
				frappe.db.commit()
				user = frappe.get_doc("User", email)
			else:
				raise
	# Ensure the tester role is attached
	existing_roles = {r.role for r in user.roles}
	if _TEST_ROLE not in existing_roles:
		user.append("roles", {"role": _TEST_ROLE})
		try:
			user.save(ignore_permissions=True)
		except Exception as e:
			if "Connection refused" in str(e):
				frappe.db.commit()
			else:
				raise


def _ensure_alfred_settings_allows_role(role: str) -> None:
	settings = frappe.get_single("Alfred Settings")
	existing = {r.role for r in settings.allowed_roles}
	if role not in existing:
		settings.append("allowed_roles", {"role": role})
		settings.save(ignore_permissions=True)


def _cleanup(alice_conv: str | None) -> None:
	frappe.set_user("Administrator")
	if alice_conv:
		# Audit Log is immutable from user session but Administrator can purge via db.delete
		frappe.db.delete("Alfred Audit Log", {"conversation": alice_conv})
		frappe.db.delete("Alfred Message", {"conversation": alice_conv})
		frappe.db.delete("Alfred Changeset", {"conversation": alice_conv})
		frappe.db.delete("Alfred Created Document", {"parent": alice_conv})
		if frappe.db.exists("Alfred Conversation", alice_conv):
			# delete_doc enqueues delete_dynamic_links which needs the RQ redis
			# queue; swallow that specific failure so cleanup still completes.
			try:
				frappe.delete_doc(
					"Alfred Conversation", alice_conv,
					force=True, ignore_permissions=True,
				)
			except Exception as e:
				if "Connection refused" in str(e) or "Redis" in str(e):
					frappe.db.delete("Alfred Conversation", {"name": alice_conv})
				else:
					raise
	# Remove allowed_roles entry we added
	try:
		settings = frappe.get_single("Alfred Settings")
		settings.allowed_roles = [r for r in settings.allowed_roles if r.role != _TEST_ROLE]
		settings.save(ignore_permissions=True)
	except Exception as e:
		if "Connection refused" not in str(e) and "Redis" not in str(e):
			raise
	# Users and role intentionally kept so repeat runs skip re-provisioning
	frappe.db.commit()


def _tolerate_queue_failure(fn):
	"""Run fn; swallow the RQ-redis ConnectionError that fires from a
	post-save enqueue (notifications, dynamic-link cleanup) when the
	bench queue redis isn't running. The primary DB write succeeds even
	when the follow-up job can't queue."""
	try:
		return fn()
	except Exception as e:
		if "Connection refused" in str(e) or "Redis" in str(e):
			frappe.db.commit()
			return None
		raise


def _expect_permission_error(label: str, fn):
	try:
		fn()
	except frappe.PermissionError:
		print(f"  {label}: correctly blocked")
		return
	except Exception as e:
		# Frappe's throw() with frappe.PermissionError is the expected class,
		# but some Frappe code paths wrap it as a generic throw, so be lenient
		# on the error text while still requiring a deny.
		msg = str(e).lower()
		if "permission" in msg or "not permitted" in msg or "owner" in msg:
			print(f"  {label}: correctly blocked ({type(e).__name__})")
			return
		raise AssertionError(
			f"{label}: expected PermissionError, got {type(e).__name__}: {e}"
		) from e
	raise AssertionError(f"{label}: endpoint did NOT raise - possible auth hole")


def run_tests():
	print("\n=== Alfred Permission / Cross-User Tests ===\n")

	from alfred_client.alfred_settings.page.alfred_chat.alfred_chat import (
		approve_changeset,
		create_conversation,
		delete_conversation,
		get_changeset,
		get_conversation_state,
		get_latest_changeset,
		get_messages,
		reject_changeset,
		send_message,
		set_conversation_mode,
		share_conversation,
	)
	from alfred_client.api.permissions import (
		changeset_has_permission,
		conversation_has_permission,
		message_has_permission,
	)

	# Fixture setup
	_ensure_role(_TEST_ROLE)
	for doctype in ("Alfred Conversation", "Alfred Message", "Alfred Changeset"):
		_ensure_role_doctype_perm(_TEST_ROLE, doctype)
	_ensure_user(_ALICE)
	_ensure_user(_BOB)
	_ensure_alfred_settings_allows_role(_TEST_ROLE)
	frappe.db.commit()

	alice_conv = None
	try:
		# ── Alice creates a conversation and a changeset ──────────────────
		print("Setup: Alice creates conversation + changeset...")
		frappe.set_user(_ALICE)
		result = create_conversation()
		alice_conv = result["name"]
		msg = send_message(alice_conv, "Create a Test DocType", mode="auto")
		# Create a Pending changeset owned by Alice
		cs = frappe.get_doc({
			"doctype": "Alfred Changeset",
			"conversation": alice_conv,
			"changes": '[{"op": "create", "doctype": "Custom Field", "data": {}}]',
			"status": "Pending",
			"dry_run_valid": 1,
		})
		cs.insert(ignore_permissions=True)
		frappe.db.commit()
		alice_cs = cs.name
		print(f"  Alice conv={alice_conv}, changeset={alice_cs}\n")

		# ── Unit-level permission function checks ─────────────────────────
		print("Test A: conversation_has_permission owner vs non-owner...")
		conv_doc = frappe.get_doc("Alfred Conversation", alice_conv)
		assert conversation_has_permission(conv_doc, "read", _ALICE) is True
		assert conversation_has_permission(conv_doc, "write", _ALICE) is True
		assert conversation_has_permission(conv_doc, "read", _BOB) in (False, None, 0)
		assert conversation_has_permission(conv_doc, "write", _BOB) in (False, None, 0)
		print("  PASSED\n")

		print("Test B: message_has_permission delegates to parent conversation...")
		msg_doc = frappe.get_doc("Alfred Message", msg["name"])
		assert message_has_permission(msg_doc, "read", _ALICE) is True
		assert message_has_permission(msg_doc, "read", _BOB) in (False, None, 0)
		print("  PASSED\n")

		print("Test C: changeset_has_permission delegates to parent conversation...")
		assert changeset_has_permission(cs, "read", _ALICE) is True
		assert changeset_has_permission(cs, "write", _BOB) in (False, None, 0)
		print("  PASSED\n")

		# ── Endpoint-level enforcement (Bob tries Alice's stuff) ──────────
		frappe.set_user(_BOB)

		print("Test 1: Bob cannot get_messages on Alice's conversation...")
		_expect_permission_error(
			"get_messages",
			lambda: get_messages(alice_conv),
		)
		print()

		print("Test 2: Bob cannot send_message to Alice's conversation...")
		_expect_permission_error(
			"send_message",
			lambda: send_message(alice_conv, "hack attempt"),
		)
		print()

		print("Test 3: Bob cannot set_conversation_mode on Alice's conversation...")
		_expect_permission_error(
			"set_conversation_mode",
			lambda: set_conversation_mode(alice_conv, "Dev"),
		)
		print()

		print("Test 4: Bob cannot delete Alice's conversation...")
		_expect_permission_error(
			"delete_conversation",
			lambda: delete_conversation(alice_conv),
		)
		print()

		print("Test 5: Bob cannot share Alice's conversation...")
		_expect_permission_error(
			"share_conversation",
			lambda: share_conversation(alice_conv, _BOB, read=1, write=1),
		)
		print()

		print("Test 6: Bob cannot get_conversation_state on Alice's conversation...")
		_expect_permission_error(
			"get_conversation_state",
			lambda: get_conversation_state(alice_conv),
		)
		print()

		print("Test 7: Bob cannot get_latest_changeset on Alice's conversation...")
		_expect_permission_error(
			"get_latest_changeset",
			lambda: get_latest_changeset(alice_conv),
		)
		print()

		print("Test 8: Bob cannot get_changeset (read) for Alice's changeset...")
		_expect_permission_error(
			"get_changeset",
			lambda: get_changeset(alice_cs),
		)
		print()

		print("Test 9: Bob cannot approve_changeset for Alice's changeset...")
		_expect_permission_error(
			"approve_changeset",
			lambda: approve_changeset(alice_cs),
		)
		print()

		print("Test 10: Bob cannot reject_changeset for Alice's changeset...")
		_expect_permission_error(
			"reject_changeset",
			lambda: reject_changeset(alice_cs),
		)
		print()

		# ── Share flow: Alice shares read-only with Bob ───────────────────
		print("Test 11: Share flow - read-only grant + write still blocked...")
		frappe.set_user(_ALICE)
		_tolerate_queue_failure(
			lambda: share_conversation(alice_conv, _BOB, read=1, write=0),
		)
		frappe.db.commit()

		frappe.set_user(_BOB)
		# Read should now succeed
		msgs = get_messages(alice_conv)
		assert isinstance(msgs, list), "Bob should now read Alice's messages"
		print(f"  Bob can read ({len(msgs)} message(s))")
		# Write must still be blocked
		_expect_permission_error(
			"send_message (read-share only)",
			lambda: send_message(alice_conv, "still blocked"),
		)
		# Approve must still be blocked (write permission required)
		_expect_permission_error(
			"approve_changeset (read-share only)",
			lambda: approve_changeset(alice_cs),
		)
		print("  PASSED\n")

		# ── Revoke share: Bob loses access instantly ──────────────────────
		print("Test 12: Share revoke - Bob loses read access...")
		frappe.set_user("Administrator")
		_tolerate_queue_failure(
			lambda: frappe.share.remove("Alfred Conversation", alice_conv, _BOB),
		)
		frappe.db.commit()

		frappe.set_user(_BOB)
		_expect_permission_error(
			"get_messages (after revoke)",
			lambda: get_messages(alice_conv),
		)
		print("  PASSED\n")

		# ── Alice still retains ownership access ──────────────────────────
		print("Test 13: Alice (owner) retains full access after share dance...")
		frappe.set_user(_ALICE)
		msgs = get_messages(alice_conv)
		assert isinstance(msgs, list)
		state = get_conversation_state(alice_conv)
		assert state["status"] in ("Open", "In Progress")
		print("  Alice read + state fetch OK")
		print("  PASSED\n")

		print("=== All permission tests PASSED ===\n")
	finally:
		_cleanup(alice_conv)
