"""Auto-scrape Frappe APIs into alfred_client/data/frappe_kb/apis.yaml.

Walks the whitelisted Frappe APIs that agent-generated code can actually call:
  - `frappe.utils.*` — the VALID_UTILS subset that safe_exec.py exposes to
    Server Scripts. Using the same whitelist means every entry in apis.yaml
    is a symbol the agent can legitimately reach for; nothing here is
    hypothetical.
  - `frappe.db.*` — the Database API. We cherry-pick the handful of
    methods agents use 99% of the time (get_value, set_value, exists,
    count, sql, get_list, get_all, get_single_value). The full Database
    class has many more but they're either SQL-builder internals or
    permission bypassers.
  - A few top-level `frappe.*` functions (get_doc, new_doc, throw, msgprint,
    sendmail, log_error, make_get_request, make_post_request).

For each symbol, we emit one FKB entry:
  - `kind: api`
  - `title` / `signature`  -- inspect-based
  - `summary`              -- first docstring paragraph, cleaned up
  - `body`                 -- full docstring
  - `keywords`             -- derived from the dotted path
  - `source`               -- "auto-scraped from frappe v<version>"

Hand-overrides in apis_overrides.yaml (same repo dir) take precedence and
win on merge - run this script after editing overrides and the generated
file reflects the overrides verbatim.

Usage (from bench root):
    bench --site dev.alfred execute alfred_client.scripts.build_kb_apis.build

The script is safe to run repeatedly; it overwrites apis.yaml each time.
"""

from __future__ import annotations

import datetime
import inspect
import logging
import re
from pathlib import Path
from typing import Any

import frappe

logger = logging.getLogger("alfred_client.build_kb_apis")


# ── Whitelist: what symbols become entries ──────────────────────────

# Mirror of safe_exec.py's VALID_UTILS. Agents can call any of these from a
# Server Script so they're the APIs worth teaching. Keep in sync if upstream
# grows the list.
_UTILS_WHITELIST = (
	"is_invalid_date_string", "getdate", "get_datetime", "to_timedelta",
	"get_timedelta", "add_to_date", "add_days", "add_months", "add_years",
	"date_diff", "month_diff", "time_diff", "time_diff_in_seconds",
	"time_diff_in_hours", "now_datetime", "get_timestamp", "get_eta",
	"get_system_timezone", "convert_utc_to_system_timezone", "now",
	"nowdate", "today", "nowtime", "get_first_day", "get_quarter_start",
	"get_quarter_ending", "get_first_day_of_week", "get_year_start",
	"get_year_ending", "get_last_day_of_week", "get_last_day", "get_time",
	"get_datetime_in_timezone", "get_datetime_str", "get_date_str",
	"get_time_str", "get_user_date_format", "get_user_time_format",
	"format_date", "format_time", "format_datetime", "format_duration",
	"get_weekdays", "get_weekday", "get_timespan_date_range",
	"global_date_format", "has_common", "flt", "cint", "floor", "ceil",
	"cstr", "rounded", "remainder", "safe_div",
	"round_based_on_smallest_currency_fraction", "encode", "parse_val",
	"fmt_money", "get_number_format_info", "money_in_words", "in_words",
	"is_html", "is_image", "strip_html", "escape_html", "pretty_date",
	"comma_or", "comma_and", "comma_sep", "new_line_sep",
	"filter_strip_join", "get_url", "get_host_name_from_request",
	"url_contains_port", "get_host_name", "get_link_to_form",
	"get_link_to_report", "get_absolute_url", "get_url_to_form",
	"get_url_to_list", "get_url_to_report", "get_url_to_report_with_filters",
	"evaluate_filters", "compare", "get_filter", "make_filter_tuple",
	"make_filter_dict", "sanitize_column", "scrub_urls",
	"expand_relative_urls", "quoted", "quote_urls", "unique", "strip",
	"to_markdown", "md_to_html", "markdown", "is_subset", "generate_hash",
	"formatdate", "get_user_info_for_avatar", "get_abbr", "get_month",
	"sha256_hash", "parse_json", "orjson_dumps",
)

# Database API methods agents reach for repeatedly. The full Database
# surface is huge (SQL builder internals, permission bypass helpers); we
# ship the handful that a correct Server Script / CrewAI action actually
# needs.
_DB_METHODS = (
	"get_value", "set_value", "get_single_value", "get_default",
	"exists", "count", "escape", "sql", "get_list", "get_all",
	"after_commit", "before_commit",
)

# Top-level frappe.* helpers agents use most. Keep this tight - every entry
# here represents agent-visible surface area. If we add something, make
# sure safe_exec.py actually exposes it.
_TOP_LEVEL = (
	("frappe", "get_doc"),
	("frappe", "new_doc"),
	("frappe", "get_all"),
	("frappe", "get_list"),
	("frappe", "get_cached_doc"),
	("frappe", "throw"),
	("frappe", "msgprint"),
	("frappe", "sendmail"),
	("frappe", "log_error"),
	("frappe", "has_permission"),
	("frappe", "rename_doc"),
	("frappe", "delete_doc"),
	("frappe", "enqueue"),
	("frappe", "copy_doc"),
	("frappe", "render_template"),
	("frappe", "get_meta"),
	("frappe", "bold"),
	("frappe.integrations.utils", "make_get_request"),
	("frappe.integrations.utils", "make_post_request"),
	("frappe.integrations.utils", "make_put_request"),
	("frappe.integrations.utils", "make_patch_request"),
	("frappe.integrations.utils", "make_delete_request"),
)


# ── Scraping helpers ────────────────────────────────────────────────

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _first_paragraph(docstring: str) -> str:
	"""Pull the first paragraph out of a Python docstring.

	Collapses whitespace inside the paragraph, strips Sphinx-style :param:
	and :returns: blocks by cutting at the first such marker.
	"""
	if not docstring:
		return ""
	# Strip leading blank lines and the common-leading-indent that inspect
	# preserves. `inspect.cleandoc` normalises both.
	cleaned = inspect.cleandoc(docstring)
	lines: list[str] = []
	for ln in cleaned.splitlines():
		stripped = ln.strip()
		if not stripped:
			break  # end of first paragraph
		if stripped.startswith((":param", ":returns", ":rtype", ":raises", "Args:", "Returns:")):
			break
		lines.append(stripped)
	return " ".join(lines)


def _signature_of(obj: Any) -> str:
	"""Best-effort inspect.signature wrapper.

	Returns just "(args)" - no name prefix - because the caller composes it
	with the fully-qualified name.
	"""
	try:
		sig = inspect.signature(obj)
	except (ValueError, TypeError):
		return "(...)"
	return str(sig)


def _keywords_from(path: str, name: str) -> list[str]:
	"""Derive retrieval keywords from a dotted path.

	`frappe.utils.add_days` -> ["add_days", "frappe.utils", "frappe.utils.add_days",
	                             "add", "days", "util", "utility"]

	The split-on-underscore bit is important: agents often phrase questions
	with spaces ("add days") instead of underscores ("add_days"), so both
	forms need to score as keyword hits.
	"""
	kws = set()
	kws.add(name)
	kws.add(path)
	kws.add(f"{path}.{name}")
	for part in name.split("_"):
		if len(part) >= 3:
			kws.add(part)
	for part in path.split("."):
		if len(part) >= 3:
			kws.add(part)
	return sorted(kws)


def _entry_id(path: str, name: str) -> str:
	"""Stable entry id - snake_case-safe. frappe.db.get_value -> api_frappe_db_get_value."""
	flat = f"{path}.{name}".replace(".", "_")
	return f"api_{flat}"


def _scrape_function(path: str, name: str, obj: Any) -> dict | None:
	"""Turn one live callable into an FKB apis.yaml entry dict."""
	if not callable(obj):
		return None
	if name.startswith("_"):
		return None  # private / dunder

	doc = inspect.getdoc(obj) or ""
	summary = _first_paragraph(doc)
	if not summary:
		# Agents need SOMETHING to reason about. Functions with no docstring
		# get a placeholder mentioning they're auto-scraped and need a hand-
		# override before they're useful. They still ship - the signature
		# alone is a small win.
		summary = f"(Auto-scraped from {path}; no upstream docstring.)"

	body = doc or summary
	sig = _signature_of(obj)

	return {
		"kind": "api",
		"title": f"{path}.{name}",
		"summary": summary,
		"keywords": _keywords_from(path, name),
		"signature": f"{path}.{name}{sig}",
		"body": body,
		"source": f"auto-scraped from frappe v{frappe.__version__}",
		"verified_on": datetime.date.today().isoformat(),
	}


def _scrape_utils() -> dict[str, dict]:
	"""Scrape the utils whitelist into {entry_id: entry_dict}."""
	out: dict[str, dict] = {}
	mod = frappe.utils.data
	for name in _UTILS_WHITELIST:
		obj = getattr(mod, name, None)
		if obj is None:
			logger.warning("utils whitelist has %r but frappe.utils.data does not export it", name)
			continue
		entry = _scrape_function("frappe.utils", name, obj)
		if entry is None:
			continue
		out[_entry_id("frappe.utils", name)] = entry
	return out


def _scrape_db() -> dict[str, dict]:
	"""Scrape the cherry-picked Database methods."""
	out: dict[str, dict] = {}
	# frappe.db is an instance of the Database class; the methods we care
	# about live on the class itself (instance bound at request time).
	# Use inspect.getmembers(type(...)) so we pick up the function objects
	# with clean signatures rather than bound methods.
	for name in _DB_METHODS:
		obj = getattr(frappe.db, name, None)
		if obj is None:
			logger.warning("db method %r not found on frappe.db", name)
			continue
		entry = _scrape_function("frappe.db", name, obj)
		if entry is None:
			continue
		out[_entry_id("frappe.db", name)] = entry
	return out


def _scrape_top_level() -> dict[str, dict]:
	"""Scrape the top-level frappe.* helpers list."""
	out: dict[str, dict] = {}
	for module_path, name in _TOP_LEVEL:
		mod = _resolve_module(module_path)
		if mod is None:
			logger.warning("could not resolve module %r", module_path)
			continue
		obj = getattr(mod, name, None)
		if obj is None:
			logger.warning("%s.%s not found", module_path, name)
			continue
		entry = _scrape_function(module_path, name, obj)
		if entry is None:
			continue
		out[_entry_id(module_path, name)] = entry
	return out


def _resolve_module(dotted: str) -> Any | None:
	"""Turn 'frappe.integrations.utils' into the module object.

	Uses importlib.import_module so submodule packages that aren't lazily
	loaded as attributes on their parent still resolve correctly. For
	example, `frappe.integrations.utils` is a subpackage that isn't exposed
	via attribute access until it's first imported, so plain __import__
	+ getattr fails. import_module handles the import side effect.
	"""
	import importlib
	try:
		return importlib.import_module(dotted)
	except ImportError as e:
		logger.warning("failed to resolve %r: %s", dotted, e)
		return None


# ── Output ──────────────────────────────────────────────────────────


def _yaml_dump(entries: dict[str, dict]) -> str:
	"""Render the FKB-shaped dict as a YAML string.

	Using yaml.safe_dump with sort_keys=False preserves the order we built -
	alphabetical within each kind. That makes diffs stable between script
	runs (important for code review).
	"""
	import yaml

	# PyYAML's safe_dump doesn't handle multi-line strings well by default;
	# the `|` block scalar preserves formatting for docstring-shaped bodies.
	def str_presenter(dumper, data):
		if "\n" in data:
			return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
		return dumper.represent_scalar("tag:yaml.org,2002:str", data)
	yaml.add_representer(str, str_presenter)

	lines: list[str] = [
		"# Auto-generated by alfred_client/scripts/build_kb_apis.py",
		"# Edit apis_overrides.yaml to curate individual entries; those win on merge.",
		"# DO NOT hand-edit this file; re-run the script after upstream changes.",
		"",
	]
	# Sort entry ids for stable diffs.
	for entry_id in sorted(entries):
		chunk = yaml.dump({entry_id: entries[entry_id]}, sort_keys=False, allow_unicode=True)
		lines.append(chunk.rstrip())
		lines.append("")
	return "\n".join(lines).rstrip() + "\n"


def _merge_overrides(auto: dict[str, dict]) -> dict[str, dict]:
	"""If apis_overrides.yaml exists, overlay it on top of the auto-scraped set.

	Overrides use the same schema as auto-scraped entries. Any entry id
	present in the overrides file wins verbatim - which means hand-curated
	examples / pitfalls replace the auto summary.
	"""
	import yaml

	overrides_path = _data_dir() / "frappe_kb" / "apis_overrides.yaml"
	if not overrides_path.exists():
		return auto

	try:
		overrides = yaml.safe_load(overrides_path.read_text()) or {}
	except Exception as e:
		logger.error("failed to parse apis_overrides.yaml: %s", e)
		return auto

	merged = dict(auto)
	for entry_id, entry in overrides.items():
		if entry_id in merged:
			logger.info("override applied to %s", entry_id)
		else:
			logger.info("override introduces new entry %s", entry_id)
		merged[entry_id] = entry
	return merged


def _data_dir() -> Path:
	return Path(frappe.get_app_path("alfred_client")) / "data"


# ── Public entry ────────────────────────────────────────────────────


@frappe.whitelist()
def build():
	"""Scrape, merge overrides, write apis.yaml. Safe to run repeatedly."""
	entries: dict[str, dict] = {}
	entries.update(_scrape_utils())
	entries.update(_scrape_db())
	entries.update(_scrape_top_level())

	merged = _merge_overrides(entries)

	output_path = _data_dir() / "frappe_kb" / "apis.yaml"
	output_path.parent.mkdir(parents=True, exist_ok=True)
	output_path.write_text(_yaml_dump(merged))

	print(f"Wrote {len(merged)} entries to {output_path}")
	return {"entries": len(merged), "path": str(output_path)}
