"""Tests for the Frappe Knowledge Base (FKB) layer.

Covers:
  - `load_kb` merges YAML sources and validates schema
  - `lookup_entry` / `list_entries` filter by kind
  - `search_keyword` uses weighted scoring (title > applies_to > keywords > body)
  - `clear_cache` forces reload
  - Missing / malformed entries are dropped with a warning, not a crash
  - The `lookup_frappe_knowledge` MCP tool entry point behaves

Run via:
    bench --site dev.alfred execute alfred_client.test_frappe_kb.run_tests
"""

import functools
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


# ── Fixtures ────────────────────────────────────────────────────────


def _write_fixture_kb(tmpdir: str) -> Path:
	"""Write a minimal frappe_kb/ tree into tmpdir and return the path.

	We use a custom fixture (not the real shipped rules.yaml) so the tests are
	deterministic even if the real KB grows or changes.
	"""
	kb_dir = Path(tmpdir) / "frappe_kb"
	kb_dir.mkdir(parents=True)

	(kb_dir / "rules.yaml").write_text(
		"server_script_no_imports:\n"
		"  kind: rule\n"
		"  title: Server Scripts cannot use import\n"
		"  summary: RestrictedPython has no __import__, so imports fail at runtime.\n"
		"  keywords: [server_script, import, restrictedpython, validate, throw]\n"
		"  applies_to: [\"Server Script\"]\n"
		"  body: |\n"
		"    - import is forbidden. Use frappe.utils, json, datetime directly.\n"
		"  verified_on: '2026-04-16'\n"
		"\n"
		"notification_email:\n"
		"  kind: rule\n"
		"  title: Notification DocType for emails, not Server Scripts\n"
		"  summary: Declarative alerts use Notification with Jinja templates.\n"
		"  keywords: [notification, email, alert, sendmail, notify]\n"
		"  applies_to: [\"Notification\"]\n"
		"  body: |\n"
		"    - Use the Notification DocType for email/alert requirements.\n"
		"  verified_on: '2026-04-16'\n"
		"\n"
		"bad_entry_missing_fields:\n"
		"  kind: rule\n"
		"  title: This entry is missing required fields\n"
		"  # intentionally no summary / keywords / body / verified_on\n"
		"\n"
		"bad_entry_invalid_kind:\n"
		"  kind: not_a_real_kind\n"
		"  title: Invalid kind\n"
		"  summary: bad\n"
		"  keywords: [x]\n"
		"  body: bad\n"
		"  verified_on: '2026-04-16'\n"
	)

	(kb_dir / "apis.yaml").write_text(
		"api_frappe_db_get_value:\n"
		"  kind: api\n"
		"  title: frappe.db.get_value\n"
		"  summary: Read one or more fields from a document without loading it.\n"
		"  keywords: [get_value, db, read, query]\n"
		"  body: |\n"
		"    frappe.db.get_value(doctype, name, fieldname)\n"
		"  verified_on: '2026-04-16'\n"
	)

	# idioms.yaml deliberately missing - loader should treat as empty
	return kb_dir


def _with_fixture(func):
	"""Decorator: create a temp fixture KB and patch _kb_dir() to point at it."""
	@functools.wraps(func)
	def wrapper():
		from alfred_client.mcp import frappe_kb

		with tempfile.TemporaryDirectory() as tmp:
			kb_dir = _write_fixture_kb(tmp)
			frappe_kb.clear_cache()
			with patch.object(frappe_kb, "_kb_dir", return_value=kb_dir):
				return func()
	return wrapper


# ── Individual tests ────────────────────────────────────────────────


@_with_fixture
def test_load_kb_merges_files_and_drops_invalid_entries():
	from alfred_client.mcp import frappe_kb

	entries = frappe_kb.load_kb()
	# Expected: 2 valid rules + 1 valid api. The two bad_entry_* rows must be
	# filtered out at load time because they fail required-field validation.
	_assert(len(entries) == 3, f"expected 3 valid entries, got {len(entries)}: {list(entries)}")
	_assert("server_script_no_imports" in entries, "server_script_no_imports present")
	_assert("notification_email" in entries, "notification_email present")
	_assert("api_frappe_db_get_value" in entries, "api_frappe_db_get_value present")
	_assert("bad_entry_missing_fields" not in entries, "missing-fields entry filtered")
	_assert("bad_entry_invalid_kind" not in entries, "invalid-kind entry filtered")

	# The id field should be injected into each entry for caller convenience.
	_assert(entries["server_script_no_imports"]["id"] == "server_script_no_imports",
		"id injected into entry")


@_with_fixture
def test_list_entries_filters_by_kind():
	from alfred_client.mcp import frappe_kb

	all_entries = frappe_kb.list_entries()
	rules = frappe_kb.list_entries(kind="rule")
	apis = frappe_kb.list_entries(kind="api")
	idioms = frappe_kb.list_entries(kind="idiom")

	_assert(len(all_entries) == 3, f"all: expected 3, got {len(all_entries)}")
	_assert(len(rules) == 2, f"rules: expected 2, got {len(rules)}")
	_assert(len(apis) == 1, f"apis: expected 1, got {len(apis)}")
	_assert(len(idioms) == 0, f"idioms: expected 0 (file missing), got {len(idioms)}")

	# list_entries returns summary, not full body
	sample = rules[0]
	_assert("title" in sample and "summary" in sample, "list returns summary fields")
	_assert("body" not in sample, "list does NOT return body (too large)")


@_with_fixture
def test_lookup_entry_by_id():
	from alfred_client.mcp import frappe_kb

	entry = frappe_kb.lookup_entry("server_script_no_imports")
	_assert(entry is not None, "existing id returns an entry")
	_assert(entry["kind"] == "rule", "kind preserved")
	_assert("import" in entry["body"].lower(), "body preserved")

	missing = frappe_kb.lookup_entry("nope_not_here")
	_assert(missing is None, "missing id returns None")


@_with_fixture
def test_search_keyword_ranks_title_over_body():
	from alfred_client.mcp import frappe_kb

	# "import" appears in the title+body of server_script_no_imports and in
	# the keywords list - should strongly top the ranking.
	results = frappe_kb.search_keyword("server script import", k=5)
	_assert(len(results) >= 1, "expected at least one hit")
	_assert(results[0]["id"] == "server_script_no_imports",
		f"expected server_script_no_imports as top hit, got {results[0]['id']}")
	_assert(results[0]["_score"] >= 5, f"score too low: {results[0]['_score']}")


@_with_fixture
def test_search_keyword_applies_to_weighting():
	from alfred_client.mcp import frappe_kb

	# "Server Script" in applies_to should boost the server_script rule even
	# without the word "import" in the query.
	results = frappe_kb.search_keyword("server script validate throw", k=3)
	_assert(results, "validation query should hit")
	_assert(results[0]["id"] == "server_script_no_imports",
		f"expected server_script rule top, got {results[0]['id']}")


@_with_fixture
def test_search_keyword_kind_filter():
	from alfred_client.mcp import frappe_kb

	# With kind=api, the server_script rule (kind=rule) must not surface.
	results = frappe_kb.search_keyword("get_value query", kind="api", k=5)
	ids = [r["id"] for r in results]
	_assert("api_frappe_db_get_value" in ids, f"api entry should match, got {ids}")
	_assert(all(r["kind"] == "api" for r in results), "all results must be kind=api")


@_with_fixture
def test_search_keyword_min_score():
	from alfred_client.mcp import frappe_kb

	# A query unrelated to anything in the KB returns no hits (min_score gate).
	results = frappe_kb.search_keyword("lorem ipsum dolor sit amet", k=5)
	_assert(results == [], f"unrelated query should return [], got {results}")


@_with_fixture
def test_search_keyword_short_terms_ignored():
	from alfred_client.mcp import frappe_kb

	# Single- and two-letter tokens are noise; the loader filters them at
	# query time so "is a" doesn't match everything containing "is".
	results = frappe_kb.search_keyword("is a to on", k=5)
	_assert(results == [], "too-short query should return []")


@_with_fixture
def test_search_keyword_empty_query():
	from alfred_client.mcp import frappe_kb

	_assert(frappe_kb.search_keyword("") == [], "empty query returns []")
	_assert(frappe_kb.search_keyword("   ") == [], "whitespace query returns []")


@_with_fixture
def test_clear_cache_reloads_on_file_change():
	from alfred_client.mcp import frappe_kb

	# First load populates the cache.
	frappe_kb.load_kb()

	# Rewrite rules.yaml with a different entry set.
	kb_dir = frappe_kb._kb_dir()
	(kb_dir / "rules.yaml").write_text(
		"brand_new_rule:\n"
		"  kind: rule\n"
		"  title: Brand new rule\n"
		"  summary: ok\n"
		"  keywords: [brand]\n"
		"  body: ok\n"
		"  verified_on: '2026-04-16'\n"
	)

	# Without mtime change the cache would still return the old data. Force
	# reload and verify the new content is picked up.
	frappe_kb.clear_cache()
	reloaded = frappe_kb.load_kb()
	_assert("brand_new_rule" in reloaded, "new entry visible after clear_cache")
	_assert("server_script_no_imports" not in reloaded,
		"old entry gone after file replaced")


# ── MCP tool entry point ────────────────────────────────────────────


@_with_fixture
def test_lookup_frappe_knowledge_search():
	from alfred_client.mcp import tools

	out = tools.lookup_frappe_knowledge("server script import")
	_assert(isinstance(out, dict), "tool returns a dict")
	_assert("entries" in out, "response has entries")
	_assert(out["mode"] == "search", "mode is search for non-empty query")
	_assert(len(out["entries"]) >= 1, "at least one hit")
	_assert(out["entries"][0]["id"] == "server_script_no_imports",
		f"top hit should be server_script_no_imports, got {out['entries'][0]['id']}")


@_with_fixture
def test_lookup_frappe_knowledge_empty_query_discovery():
	from alfred_client.mcp import tools

	out = tools.lookup_frappe_knowledge("", kind="rule")
	_assert(out["mode"] == "list", "empty query switches to list mode")
	_assert(len(out["entries"]) == 2, f"two rule entries, got {len(out['entries'])}")


@_with_fixture
def test_lookup_frappe_knowledge_invalid_kind():
	from alfred_client.mcp import tools

	out = tools.lookup_frappe_knowledge("x", kind="bogus")
	_assert(out.get("error") == "invalid_argument",
		f"expected invalid_argument error, got {out}")


# ── Runner ──────────────────────────────────────────────────────────


ALL_TESTS = [
	test_load_kb_merges_files_and_drops_invalid_entries,
	test_list_entries_filters_by_kind,
	test_lookup_entry_by_id,
	test_search_keyword_ranks_title_over_body,
	test_search_keyword_applies_to_weighting,
	test_search_keyword_kind_filter,
	test_search_keyword_min_score,
	test_search_keyword_short_terms_ignored,
	test_search_keyword_empty_query,
	test_clear_cache_reloads_on_file_change,
	test_lookup_frappe_knowledge_search,
	test_lookup_frappe_knowledge_empty_query_discovery,
	test_lookup_frappe_knowledge_invalid_kind,
]


@frappe.whitelist()
def run_tests():
	"""Run all FKB tests. Called via `bench ... execute ...test_frappe_kb.run_tests`."""
	failures = []
	for test in ALL_TESTS:
		name = test.__name__
		try:
			test()
			print(f"  PASS  {name}")
		except AssertionError as e:
			print(f"  FAIL  {name}: {e}")
			failures.append(name)
		except Exception as e:
			print(f"  ERROR {name}: {type(e).__name__}: {e}")
			failures.append(name)

	total = len(ALL_TESTS)
	passed = total - len(failures)
	print(f"\nFrappe KB tests: {passed}/{total} passed")
	if failures:
		print(f"Failed: {', '.join(failures)}")
		raise AssertionError(f"{len(failures)} test(s) failed")
