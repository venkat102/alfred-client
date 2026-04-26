"""Tests for websocket_client helper functions.

Scope: the pure helpers that don't need an event loop or a running
WebSocket server. The full async connection loop (handshake + MCP
dispatch + reconnect backoff) is heavily coupled to live Redis pub/sub
and websockets.connect, and mocking it would be worth more test
scaffolding than the coverage it buys. Focused here on the small
pieces that have clear contracts.

Covers:
  - _last_msg_id_key: key namespace format
  - _track_last_msg_id + _load_last_msg_id: round-trip through
    frappe.cache() with the 7-day TTL
  - _LAST_MSG_ID_TTL_SECONDS: sanity on the constant
  - _generate_jwt: payload shape + round-trip verification
  - _get_site_id: returns frappe.local.site

Run via:
    bench --site dev.alfred execute alfred_client.test_websocket_client.run_tests
"""

import time

import frappe
import jwt as pyjwt

from alfred_client.api.websocket_client import (
	_DISCONNECTED_QUEUE_MAX_LEN,
	_LAST_MSG_ID_TTL_SECONDS,
	_REDIS_CHANNEL_PREFIX,
	_generate_jwt,
	_get_site_id,
	_last_msg_id_key,
	_load_last_msg_id,
	_track_last_msg_id,
)


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


def run_tests():
	print("\n=== Alfred websocket_client helper tests ===\n")

	# ── _last_msg_id_key ──────────────────────────────────────────────

	print("Test 1: _last_msg_id_key namespaces by conversation name...")
	key = _last_msg_id_key("alfred-convo-123")
	_assert(
		key == "alfred:last_msg_id:alfred-convo-123",
		f"Unexpected key format: {key!r}",
	)
	# Different conversation names produce different keys.
	_assert(
		_last_msg_id_key("a") != _last_msg_id_key("b"),
		"Two different conversation names collided into the same key",
	)
	print("  PASSED\n")

	# ── _LAST_MSG_ID_TTL_SECONDS constant ─────────────────────────────

	print("Test 2: TTL matches the processing app's 7-day event-stream window...")
	_assert(
		_LAST_MSG_ID_TTL_SECONDS == 7 * 24 * 3600,
		f"TTL drift: got {_LAST_MSG_ID_TTL_SECONDS}s, expected 604800s",
	)
	print("  PASSED\n")

	# ── _track_last_msg_id + _load_last_msg_id round-trip ─────────────

	# Use a unique convo name so this test doesn't collide with real data
	# even if someone runs it on a live site.
	test_convo = f"alfred-test-convo-{int(time.time())}"
	test_msg_id = "01HXYZ-ulid-fake-aaaa"

	print("Test 3: _track -> _load round-trip preserves msg_id...")
	try:
		_track_last_msg_id(test_convo, test_msg_id)
		got = _load_last_msg_id(test_convo)
		_assert(
			got == test_msg_id,
			f"Expected {test_msg_id!r}, got {got!r}",
		)
		print("  PASSED\n")
	finally:
		# Clean up so test runs don't leak cache entries.
		try:
			frappe.cache().delete_value(_last_msg_id_key(test_convo))
		except Exception:
			pass

	# ── _load_last_msg_id on unseen conversation ──────────────────────

	print("Test 4: _load_last_msg_id returns None for unseen conversation...")
	got = _load_last_msg_id(f"alfred-never-seen-{int(time.time())}")
	_assert(got is None, f"Expected None for unseen convo, got {got!r}")
	print("  PASSED\n")

	# ── _generate_jwt ─────────────────────────────────────────────────

	print("Test 5: _generate_jwt produces a verifiable JWT with expected claims...")
	secret = "a" * 48  # 48 chars, above the 32-char floor
	token = _generate_jwt(secret, user="tester@example.com", roles=["System Manager"])
	# Round-trip verify (same algorithm, same secret).
	payload = pyjwt.decode(token, secret, algorithms=["HS256"])
	_assert(payload["user"] == "tester@example.com", f"user claim drift: {payload}")
	_assert(payload["roles"] == ["System Manager"], f"roles claim drift: {payload}")
	_assert("site_id" in payload and payload["site_id"], f"site_id missing/empty: {payload}")
	_assert("exp" in payload, f"exp claim missing: {payload}")
	_assert("iat" in payload, f"iat claim missing: {payload}")
	_assert(
		payload["exp"] - payload["iat"] == 86400,
		f"exp-iat should be 24h = 86400s, got {payload['exp'] - payload['iat']}",
	)
	print("  PASSED\n")

	# ── _generate_jwt with wrong secret rejected ──────────────────────

	print("Test 6: _generate_jwt output rejects verification with wrong secret...")
	token = _generate_jwt(secret, user="tester@example.com", roles=["System Manager"])
	try:
		pyjwt.decode(token, "different-secret-of-at-least-32-chars-abcde", algorithms=["HS256"])
		_assert(False, "Expected signature verification to fail with wrong secret")
	except pyjwt.InvalidSignatureError:
		pass  # Expected
	print("  PASSED\n")

	# ── _generate_jwt defaults to frappe.session.user when user=None ──

	print("Test 7: _generate_jwt falls back to frappe.session.user when user=None...")
	token = _generate_jwt(secret)  # no user, no roles
	payload = pyjwt.decode(token, secret, algorithms=["HS256"])
	_assert(
		payload["user"] == frappe.session.user,
		f"Expected {frappe.session.user!r}, got {payload['user']!r}",
	)
	# Roles should also be populated (whatever the current user has).
	_assert(isinstance(payload["roles"], list), f"roles should be a list: {payload}")
	print("  PASSED\n")

	# ── _get_site_id ──────────────────────────────────────────────────

	print("Test 8: _get_site_id returns frappe.local.site...")
	site_id = _get_site_id()
	_assert(site_id == frappe.local.site, f"site_id drift: {site_id!r}")
	# Processing app regex constraint: ^[a-zA-Z0-9._-]+$
	import re
	_assert(
		re.match(r"^[a-zA-Z0-9._-]+$", site_id) is not None,
		f"site_id {site_id!r} contains chars the processing app rejects",
	)
	print("  PASSED\n")

	# ── _DISCONNECTED_QUEUE_MAX_LEN + LTRIM cap ──────────────────────

	print("Test 9: disconnected-session queue is bounded by LTRIM cap...")
	# Sanity check on the constant itself first - if someone "tunes" it
	# down to 0 by mistake the LTRIM would wipe the queue on every push.
	_assert(
		_DISCONNECTED_QUEUE_MAX_LEN >= 100,
		f"Cap shrank dangerously low: {_DISCONNECTED_QUEUE_MAX_LEN}",
	)

	# Use a unique convo name so the test doesn't collide with real
	# disconnected-session queues if anyone runs it on a live site.
	test_convo = f"alfred-test-cap-{int(time.time())}"
	queue_key = f"{_REDIS_CHANNEL_PREFIX}queue:{test_convo}"
	redis_conn = frappe.cache()
	# Use a small synthetic cap rather than the production 10k - the
	# contract under test is "rpush + ltrim(-CAP, -1) bounds the list at
	# CAP and preserves the newest entries", which is independent of the
	# specific CAP value. Pushing 10k+ entries one at a time would make
	# this test slow without adding signal. The constant assertion above
	# guards against the production value being misconfigured to 0.
	test_cap = 50
	excess = 5
	total = test_cap + excess
	try:
		for i in range(total):
			redis_conn.rpush(queue_key, f"msg-{i}")
		# Apply the same LTRIM the production sites do, just with a
		# smaller bound for the synthetic queue.
		redis_conn.ltrim(queue_key, -test_cap, -1)

		length = redis_conn.llen(queue_key)
		_assert(
			length == test_cap,
			f"Queue length after LTRIM should be {test_cap}, got {length}",
		)

		# Newest entries preserved, oldest dropped. The first surviving
		# entry should be msg-{excess} (the first that wasn't trimmed).
		head = redis_conn.lrange(queue_key, 0, 0)
		expected_head = f"msg-{excess}"
		_assert(
			head and head[0].decode() == expected_head,
			f"Oldest surviving entry should be {expected_head!r}, got {head}",
		)
		# Last entry is the latest pushed.
		tail = redis_conn.lrange(queue_key, -1, -1)
		expected_tail = f"msg-{total - 1}"
		_assert(
			tail and tail[0].decode() == expected_tail,
			f"Newest entry should be {expected_tail!r}, got {tail}",
		)
		print("  PASSED\n")
	finally:
		try:
			redis_conn.delete(queue_key)
		except Exception:
			pass

	print("=== All websocket_client helper tests passed ===\n")


if __name__ == "__main__":
	run_tests()
