"""CAS race-condition tests for apply_changeset.

Isolates the lock-acquisition audit from the rest of test_deploy.py because
test_deploy.py's rollback tests need bench's RQ redis queue (for the audit
log / delete_doc / publish_realtime enqueues) and will crash without it.
The CAS guard itself only touches the DB, so it's testable in any env.

The "race" here is simulated deterministically: we pre-flip the status to
the state a concurrent process would have reached, then call apply_changeset
and assert it bails out with a clear message. If the CAS guard is broken
(as it was before: just reloading and checking status == 'Deploying' would
pass for both racing processes), apply_changeset would silently proceed on
a changeset already held by another process.

Run with: bench --site dev.alfred execute alfred_client.test_cas_race.run_tests
"""

from __future__ import annotations

import json

import frappe


def _make_conv() -> str:
	conv = frappe.get_doc({
		"doctype": "Alfred Conversation",
		"user": "Administrator",
		"status": "Open",
	})
	conv.insert(ignore_permissions=True)
	frappe.db.commit()
	return conv.name


def _make_changeset(conv_name: str, status: str = "Approved") -> str:
	cs = frappe.get_doc({
		"doctype": "Alfred Changeset",
		"conversation": conv_name,
		"status": status,
		"changes": json.dumps([]),
	})
	cs.insert(ignore_permissions=True)
	frappe.db.commit()
	return cs.name


def _cleanup(conv_name: str) -> None:
	frappe.db.sql("DELETE FROM `tabAlfred Changeset` WHERE conversation = %s", conv_name)
	frappe.db.sql("DELETE FROM `tabAlfred Audit Log` WHERE conversation = %s", conv_name)
	try:
		frappe.delete_doc("Alfred Conversation", conv_name, force=True, ignore_permissions=True)
	except Exception as e:
		if "Connection refused" in str(e) or "Redis" in str(e):
			frappe.db.delete("Alfred Conversation", {"name": conv_name})
		else:
			raise
	frappe.db.commit()


def run_tests():
	print("\n=== Alfred CAS Race Tests ===\n")

	from alfred_client.api.deploy import apply_changeset

	conv = _make_conv()
	try:
		# ── 1. Simulate another process already flipped to Deploying ───
		print("Test 1: apply_changeset bails when status is already Deploying...")
		cs_name = _make_changeset(conv)
		# Simulate process A having already won the lock
		frappe.db.sql(
			"UPDATE `tabAlfred Changeset` SET status='Deploying' WHERE name=%s",
			cs_name,
		)
		frappe.db.commit()

		try:
			apply_changeset(cs_name)
			raise AssertionError(
				"apply_changeset silently proceeded on an already-Deploying changeset - "
				"CAS race guard is broken"
			)
		except AssertionError:
			raise
		except Exception as e:
			msg = str(e).lower()
			# apply_changeset throws the first guard's error message: status
			# is already "Deploying", so `changeset.status != "Approved"`
			# fails BEFORE the CAS itself runs. That's still a correct reject.
			assert any(
				kw in msg for kw in ("approved", "already", "another process", "cannot be deployed")
			), f"Unexpected error: {e}"
			print(f"  Correctly blocked: {str(e)[:100]}")
		print("  PASSED\n")

		# ── 2. True race: two apply_changesets stepping through the CAS ─
		print("Test 2: CAS rowcount detects a concurrent winner...")
		# Put the changeset back to Approved so apply_changeset reaches the CAS.
		# Then patch frappe.db._cursor.rowcount to 0 AFTER the UPDATE fires,
		# simulating another process having completed the UPDATE first.
		frappe.db.sql(
			"UPDATE `tabAlfred Changeset` SET status='Approved' WHERE name=%s",
			cs_name,
		)
		frappe.db.commit()

		# Monkey-patch rowcount via a wrapping descriptor on the cursor
		real_sql = frappe.db.sql

		def fake_sql(query, *args, **kwargs):
			result = real_sql(query, *args, **kwargs)
			# Only zero the rowcount when we just ran the CAS UPDATE. Let
			# every other query report its true rowcount.
			if "SET status='Deploying'" in str(query):
				# Simulate "another process flipped the row before us" by
				# also committing the Deploying state from a second connection.
				# For a deterministic unit-style test we just stub rowcount.
				try:
					frappe.db._cursor.rowcount = 0
				except Exception:
					pass
			return result

		frappe.db.sql = fake_sql
		try:
			try:
				apply_changeset(cs_name)
				raise AssertionError(
					"apply_changeset should have thrown when CAS rowcount=0 - "
					"race-winner detection broken"
				)
			except AssertionError:
				raise
			except Exception as e:
				msg = str(e).lower()
				assert any(
					kw in msg for kw in ("another process", "already", "cannot be deployed")
				), f"Unexpected error: {e}"
				print(f"  Correctly blocked: {str(e)[:100]}")
		finally:
			frappe.db.sql = real_sql
		print("  PASSED\n")

		# ── 3. Rejected-status is rejected BEFORE the CAS ──────────────
		print("Test 3: Rejected changeset cannot be deployed...")
		rej = _make_changeset(conv, status="Rejected")
		try:
			apply_changeset(rej)
			raise AssertionError("Should have rejected a Rejected changeset")
		except AssertionError:
			raise
		except Exception as e:
			assert "approved" in str(e).lower()
			print(f"  Correctly rejected: {str(e)[:80]}")
		print("  PASSED\n")

		print("=== All CAS race tests PASSED ===\n")
	finally:
		_cleanup(conv)
