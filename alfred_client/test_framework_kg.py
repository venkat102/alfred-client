"""Tests for the Framework Knowledge Graph layer.

Covers:
  - `_extract_doctype` produces the expected subset from a DocType JSON
  - `build_knowledge_graph` walks a mock app tree and aggregates records
  - `_load_kg` / `_load_patterns` cache by mtime
  - `lookup_framework_doctype`, `list_framework_doctypes`, `search_framework_knowledge`
  - The `lookup_doctype` / `lookup_pattern` MCP tool entry points

Run via:
    bench --site dev.alfred execute alfred_client.test_framework_kg.run_tests
"""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import frappe


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


def _notification_like_json(name: str, app: str = "frappe", is_submittable: int = 0) -> dict:
	"""Fixture: a realistic DocType JSON subset."""
	return {
		"doctype": "DocType",
		"name": name,
		"module": "Email",
		"is_submittable": is_submittable,
		"istable": 0,
		"autoname": "Prompt",
		"track_changes": 1,
		"fields": [
			{
				"fieldname": "subject",
				"fieldtype": "Data",
				"label": "Subject",
				"reqd": 1,
			},
			{
				"fieldname": "event",
				"fieldtype": "Select",
				"label": "Event",
				"options": "New\nSave\nSubmit",
				"reqd": 1,
			},
			{
				"fieldname": "section_break_1",
				"fieldtype": "Section Break",
			},
			{
				"fieldname": "recipients",
				"fieldtype": "Table",
				"options": "Notification Recipient",
			},
		],
		"permissions": [
			{"role": "System Manager", "read": 1, "write": 1, "create": 1},
		],
	}


# ── _extract_doctype ────────────────────────────────────────────────


def test_extract_doctype_keeps_essentials():
	from alfred_client.mcp.framework_kg import _extract_doctype

	record = _extract_doctype(_notification_like_json("Notification"), app="frappe")
	_assert(record is not None, "should extract a record")
	_assert(record["name"] == "Notification", "name preserved")
	_assert(record["app"] == "frappe", "app preserved")
	_assert(record["is_submittable"] == 0, "is_submittable is int 0")
	# Section Break should be dropped from fields
	fieldnames = [f["fieldname"] for f in record["fields"]]
	_assert("subject" in fieldnames, "subject kept")
	_assert("event" in fieldnames, "event kept")
	_assert("recipients" in fieldnames, "recipients kept")
	_assert("section_break_1" not in fieldnames, "Section Break stripped")
	# Field stripping keeps only allowed keys
	event = next(f for f in record["fields"] if f["fieldname"] == "event")
	_assert(event["options"] == "New\nSave\nSubmit", "options preserved")
	_assert(event["reqd"] == 1, "reqd preserved")


def test_extract_doctype_rejects_non_doctype():
	from alfred_client.mcp.framework_kg import _extract_doctype

	non_doctype = {"doctype": "DocPerm", "name": "Something"}
	_assert(_extract_doctype(non_doctype, app="frappe") is None,
		"DocPerm should not be extracted as a DocType")


def test_extract_doctype_handles_missing_name():
	from alfred_client.mcp.framework_kg import _extract_doctype

	missing_name = {"doctype": "DocType", "fields": []}
	_assert(_extract_doctype(missing_name, app="frappe") is None,
		"DocType without name should be skipped")


def test_extract_doctype_preserves_submittable_flag():
	from alfred_client.mcp.framework_kg import _extract_doctype

	record = _extract_doctype(
		_notification_like_json("Sales Order", is_submittable=1),
		app="erpnext",
	)
	_assert(record["is_submittable"] == 1, "submittable flag preserved")


# ── build_knowledge_graph (mocked walker) ───────────────────────────


def test_build_knowledge_graph_walks_mock_tree():
	from alfred_client.mcp import framework_kg

	with tempfile.TemporaryDirectory() as tmp:
		app_path = Path(tmp)
		# Fake layout: <app>/<module>/doctype/<name>/<name>.json
		notif_dir = app_path / "email" / "doctype" / "notification"
		notif_dir.mkdir(parents=True)
		(notif_dir / "notification.json").write_text(
			json.dumps(_notification_like_json("Notification"))
		)

		sales_dir = app_path / "selling" / "doctype" / "sales_order"
		sales_dir.mkdir(parents=True)
		(sales_dir / "sales_order.json").write_text(
			json.dumps(_notification_like_json("Sales Order", is_submittable=1))
		)

		with patch.object(framework_kg, "_iter_doctype_json_files",
						  return_value=list((app_path).rglob("doctype/*/*.json"))):
			with patch.object(frappe, "get_installed_apps",
							  return_value=["mock_app"]):
				with patch.object(frappe, "get_app_path",
								  return_value=str(app_path)):
					kg = framework_kg.build_knowledge_graph(write=False)

		_assert(len(kg) == 2, f"expected 2 doctypes, got {len(kg)}")
		_assert("Notification" in kg, "Notification extracted")
		_assert("Sales Order" in kg, "Sales Order extracted")
		_assert(kg["Sales Order"]["is_submittable"] == 1, "submittable flag carried")


# ── Cache invalidation ─────────────────────────────────────────────


def test_kg_cache_reloads_on_mtime_change():
	from alfred_client.mcp import framework_kg

	with tempfile.TemporaryDirectory() as tmp:
		kg_file = Path(tmp) / "framework_kg.json"
		kg_file.write_text(json.dumps({"Notification": {"name": "Notification", "fields": []}}))

		with patch.object(framework_kg, "_kg_json_path", return_value=kg_file):
			framework_kg.clear_caches()
			kg1 = framework_kg._load_kg()
			_assert("Notification" in kg1, "first load has Notification")

			# Rewrite with new data and bump mtime (sleep 1ms then touch)
			time.sleep(0.01)
			kg_file.write_text(json.dumps({"Sales Order": {"name": "Sales Order", "fields": []}}))
			kg2 = framework_kg._load_kg()
			_assert("Sales Order" in kg2, "cache picked up the new file contents")
			_assert("Notification" not in kg2, "old entry evicted")


# ── Patterns loader ────────────────────────────────────────────────


def test_patterns_missing_file_returns_empty():
	from alfred_client.mcp import framework_kg

	with tempfile.TemporaryDirectory() as tmp:
		missing = Path(tmp) / "does_not_exist.yaml"
		with patch.object(framework_kg, "_patterns_yaml_path", return_value=missing):
			framework_kg.clear_caches()
			patterns = framework_kg._load_patterns()
			_assert(patterns == {}, "missing patterns file returns empty dict")


def test_patterns_loader_with_real_file():
	"""If pyyaml is installed and the real patterns file exists, we should get > 0 entries."""
	from alfred_client.mcp import framework_kg
	framework_kg.clear_caches()
	patterns = framework_kg._load_patterns()
	# The shipped file should have 5 starter patterns. If pyyaml is missing,
	# we tolerate an empty dict rather than erroring - the MCP tool already
	# handles that case.
	if patterns:
		_assert("approval_notification" in patterns,
			"approval_notification pattern should be in the starter library")
		assert isinstance(patterns["approval_notification"], dict)


# ── Public lookups ─────────────────────────────────────────────────


def test_lookup_framework_doctype_returns_none_when_missing():
	from alfred_client.mcp import framework_kg

	with tempfile.TemporaryDirectory() as tmp:
		kg_file = Path(tmp) / "framework_kg.json"
		kg_file.write_text(json.dumps({"Notification": {"name": "Notification", "fields": []}}))
		with patch.object(framework_kg, "_kg_json_path", return_value=kg_file):
			framework_kg.clear_caches()
			_assert(framework_kg.lookup_framework_doctype("Notification") is not None)
			_assert(framework_kg.lookup_framework_doctype("DoesNotExist") is None)


def test_list_framework_doctypes_filters_by_app():
	from alfred_client.mcp import framework_kg

	with tempfile.TemporaryDirectory() as tmp:
		kg_file = Path(tmp) / "framework_kg.json"
		kg_file.write_text(json.dumps({
			"Notification": {"name": "Notification", "app": "frappe", "module": "Email", "is_submittable": 0},
			"Sales Order": {"name": "Sales Order", "app": "erpnext", "module": "Selling", "is_submittable": 1},
		}))
		with patch.object(framework_kg, "_kg_json_path", return_value=kg_file):
			framework_kg.clear_caches()
			all_records = framework_kg.list_framework_doctypes()
			_assert(len(all_records) == 2, "list should return both")
			erpnext_only = framework_kg.list_framework_doctypes(app="erpnext")
			_assert(len(erpnext_only) == 1, "erpnext filter returns only one")
			_assert(erpnext_only[0]["name"] == "Sales Order")


# ── Search relevance ───────────────────────────────────────────────


def test_search_ranks_pattern_hits_by_term_count():
	from alfred_client.mcp import framework_kg

	with tempfile.TemporaryDirectory() as tmp:
		# Small KG + pattern set for deterministic search
		kg_file = Path(tmp) / "kg.json"
		pat_file = Path(tmp) / "patterns.yaml"
		kg_file.write_text(json.dumps({
			"Expense Claim": {"name": "Expense Claim", "app": "hrms", "module": "Expenses", "is_submittable": 1},
			"Leave Application": {"name": "Leave Application", "app": "hrms", "module": "Leaves", "is_submittable": 1},
		}))
		# Write the real YAML format so _load_patterns picks it up
		pat_file.write_text(
			"approval_notification:\n"
			"  description: Email the approver when a document needs approval\n"
			"  category: notification\n"
			"  keywords: [approver, approval, review, notify]\n"
			"  when_to_use: recipient is the approver who submits the document\n"
		)
		with patch.object(framework_kg, "_kg_json_path", return_value=kg_file):
			with patch.object(framework_kg, "_patterns_yaml_path", return_value=pat_file):
				framework_kg.clear_caches()
				# Query should find the approval_notification pattern
				result = framework_kg.search_framework_knowledge("approver notification")
				_assert(result["patterns"], "expected at least one pattern hit")
				_assert(result["patterns"][0]["name"] == "approval_notification",
					f"top hit should be approval_notification, got {result['patterns'][0]['name']}")


def test_search_empty_query_returns_empty():
	from alfred_client.mcp import framework_kg
	_assert(framework_kg.search_framework_knowledge("") == {"doctypes": [], "patterns": []})


def test_search_validation_prompt_picks_server_script_over_notification():
	"""Regression: Employee age validation prompt must match
	validation_server_script, NOT post_approval_notification.

	Pre-fix, post_approval_notification had `employee` in its keywords
	(a domain-example leak) and search scoring treated all field hits
	equally. A query containing 'employee' + 'validation' tied the two
	patterns, and the alphabetical tiebreaker gave the notification
	pattern the win. Agent then adapted a notification template for an
	Employee Extension doctype, producing a DocType+Notification pair
	instead of a Server Script.

	The fix: remove domain-example keywords from patterns, add
	validation-specific keywords (throw, restrict, reject, age, etc.)
	to validation_server_script, and weight name/keyword matches higher
	than description matches.
	"""
	from alfred_client.mcp import framework_kg

	with tempfile.TemporaryDirectory() as tmp:
		kg_file = Path(tmp) / "kg.json"
		pat_file = Path(tmp) / "patterns.yaml"
		kg_file.write_text(json.dumps({
			"Employee": {"name": "Employee", "app": "hrms", "module": "HR", "is_submittable": 0},
		}))
		# Real keyword sets from customization_patterns.yaml (post-fix)
		pat_file.write_text(
			"post_approval_notification:\n"
			"  description: Email the requester or downstream team AFTER a document is approved or submitted.\n"
			"  category: notification\n"
			"  keywords: [approved, submitted, confirmed, downstream, requester, notify_after, after_approval]\n"
			"  when_to_use: recipient should hear back that their request was approved\n"
			"\n"
			"validation_server_script:\n"
			"  description: Block a document from saving unless a custom rule passes. Use when user asks to validate restrict reject throw block or prevent saves.\n"
			"  category: script\n"
			"  keywords: [validate, validation, check, enforce, require, prevent, block, rule, throw, restrict, reject, forbid, constraint, condition, disallow, age, minimum, maximum, not_allowed, blocklist]\n"
			"  when_to_use: enforce a rule that cannot be expressed as a simple field constraint\n"
		)
		with patch.object(framework_kg, "_kg_json_path", return_value=kg_file):
			with patch.object(framework_kg, "_patterns_yaml_path", return_value=pat_file):
				framework_kg.clear_caches()
				# The exact user prompt shape that failed pre-fix
				result = framework_kg.search_framework_knowledge(
					"add a validation to the employee doctype for any employee only above the age of 24 can be created if the employees age is lessthan 24 then restrict and throw a message"
				)
				_assert(
					result["patterns"],
					"expected at least one pattern hit for validation prompt"
				)
				top_name = result["patterns"][0]["name"]
				_assert(
					top_name == "validation_server_script",
					f"top hit should be validation_server_script, got {top_name!r}",
				)
				# validation_server_script should beat post_approval_notification
				# by a LARGE margin (many keyword hits vs none)
				top_score = result["patterns"][0]["_score"]
				other_scores = [p["_score"] for p in result["patterns"][1:]]
				if other_scores:
					_assert(
						top_score >= 3 * max(other_scores),
						f"top score {top_score} should dominate runner-up {max(other_scores)} by 3x or more",
					)


# ── MCP tool entry points ──────────────────────────────────────────


def test_lookup_doctype_tool_framework_layer():
	from alfred_client.mcp import framework_kg
	from alfred_client.mcp.tools import lookup_doctype

	with tempfile.TemporaryDirectory() as tmp:
		kg_file = Path(tmp) / "kg.json"
		kg_file.write_text(json.dumps({
			"Notification": {"name": "Notification", "app": "frappe", "fields": [
				{"fieldname": "subject", "fieldtype": "Data"},
			]},
		}))
		with patch.object(framework_kg, "_kg_json_path", return_value=kg_file):
			framework_kg.clear_caches()
			result = lookup_doctype("Notification", layer="framework")
			_assert(isinstance(result, dict), "tool returns dict")
			_assert(result.get("name") == "Notification", "framework layer returns vanilla record")

			missing = lookup_doctype("NonExistent", layer="framework")
			_assert(missing.get("error") == "not_found", "missing doctype returns error")


def test_lookup_doctype_tool_invalid_layer():
	from alfred_client.mcp.tools import lookup_doctype
	result = lookup_doctype("Notification", layer="bogus")
	_assert(result.get("error") == "invalid_argument",
		f"invalid layer should error, got {result}")


def test_lookup_pattern_tool_kinds():
	from alfred_client.mcp import framework_kg
	from alfred_client.mcp.tools import lookup_pattern

	with tempfile.TemporaryDirectory() as tmp:
		pat_file = Path(tmp) / "patterns.yaml"
		pat_file.write_text(
			"approval_notification:\n"
			"  description: Email the approver\n"
			"  category: notification\n"
			"  keywords: [approver]\n"
			"  when_to_use: approver recipient\n"
			"  template: {}\n"
		)
		with patch.object(framework_kg, "_patterns_yaml_path", return_value=pat_file):
			framework_kg.clear_caches()

			# kind=name exact
			by_name = lookup_pattern("approval_notification", kind="name")
			_assert("pattern" in by_name, f"name kind returns pattern key, got {by_name}")

			# kind=name missing
			missing = lookup_pattern("does_not_exist", kind="name")
			_assert(missing.get("error") == "not_found")

			# kind=list
			listed = lookup_pattern("", kind="list")
			_assert("patterns" in listed, "list kind returns patterns key")
			_assert(len(listed["patterns"]) == 1)

			# kind=search
			search = lookup_pattern("approver", kind="search")
			_assert("patterns" in search, "search returns patterns list")

			# kind=all - exact match wins
			all_match = lookup_pattern("approval_notification", kind="all")
			_assert(all_match.get("source") == "exact")

			# kind=all - fallback to search
			all_fallback = lookup_pattern("approver", kind="all")
			_assert(all_fallback.get("source") == "search")


# ── Test runner ────────────────────────────────────────────────────


def run_tests():
	"""Run all tests. Call via `bench --site dev.alfred execute alfred_client.test_framework_kg.run_tests`."""
	tests = [
		test_extract_doctype_keeps_essentials,
		test_extract_doctype_rejects_non_doctype,
		test_extract_doctype_handles_missing_name,
		test_extract_doctype_preserves_submittable_flag,
		test_build_knowledge_graph_walks_mock_tree,
		test_kg_cache_reloads_on_mtime_change,
		test_patterns_missing_file_returns_empty,
		test_patterns_loader_with_real_file,
		test_lookup_framework_doctype_returns_none_when_missing,
		test_list_framework_doctypes_filters_by_app,
		test_search_ranks_pattern_hits_by_term_count,
		test_search_empty_query_returns_empty,
		test_search_validation_prompt_picks_server_script_over_notification,
		test_lookup_doctype_tool_framework_layer,
		test_lookup_doctype_tool_invalid_layer,
		test_lookup_pattern_tool_kinds,
	]
	passed = 0
	failed = []
	for test_func in tests:
		try:
			test_func()
			print(f"  PASS: {test_func.__name__}")
			passed += 1
		except Exception as e:
			print(f"  FAIL: {test_func.__name__}: {e}")
			failed.append((test_func.__name__, str(e)))
	print(f"\n{passed}/{len(tests)} passed")
	if failed:
		print(f"\nFailures:")
		for name, err in failed:
			print(f"  {name}: {err}")
		raise AssertionError(f"{len(failed)} test(s) failed")
