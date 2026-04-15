"""Frappe Framework Knowledge Graph - vanilla DocType metadata + customization patterns.

Two-layer knowledge store:
  1. Extracted framework facts (auto) - walks every installed bench app's
     doctype/*/*.json and extracts a subset of each vanilla DocType definition
     (fields, is_submittable, permissions, autoname rule). Output at
     `alfred_client/data/framework_kg.json`. Gitignored, regenerated at migrate time.

  2. Curated customization patterns (human) - hand-written YAML of common
     Frappe customization idioms (approval notification, validation script,
     audit log, etc.). Shipped at `alfred_client/data/customization_patterns.yaml`.
     Committed to the repo.

Both layers are exposed via MCP tools (`lookup_doctype`, `lookup_pattern`) so
agents query on demand instead of having framework facts hardcoded in prompts.

Both layers are loaded lazily on first tool call and cached in-process keyed by
file mtime, so file edits are picked up without a restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import frappe

logger = logging.getLogger("alfred_client.framework_kg")


# ── File location ───────────────────────────────────────────────────


def _data_dir() -> Path:
	"""Directory where the KG files live. Resolved via frappe.get_app_path."""
	return Path(frappe.get_app_path("alfred_client")) / "data"


def _kg_json_path() -> Path:
	return _data_dir() / "framework_kg.json"


def _patterns_yaml_path() -> Path:
	return _data_dir() / "customization_patterns.yaml"


# ── Field-level extraction ──────────────────────────────────────────
#
# DocType JSONs are large (a typical one is 200-2000 lines). We strip
# everything except what an AI agent needs to generate correct customizations.
# Keeping only these fields brings the KG file size down from ~20 MB to ~2 MB.

_FIELD_KEEP_KEYS = {
	"fieldname",
	"fieldtype",
	"label",
	"options",
	"reqd",
	"default",
	"in_list_view",
	"read_only",
	"hidden",
	"depends_on",
	"description",
	"fetch_from",
	"unique",
}

_PERMISSION_KEEP_KEYS = {
	"role",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"if_owner",
	"permlevel",
}


def _strip_field(field: dict) -> dict:
	"""Keep only the keys an AI agent needs to understand a field."""
	return {k: v for k, v in field.items() if k in _FIELD_KEEP_KEYS}


def _strip_permission(perm: dict) -> dict:
	return {k: v for k, v in perm.items() if k in _PERMISSION_KEEP_KEYS}


def _extract_doctype(raw: dict, app: str) -> dict | None:
	"""Build the KG record for one DocType JSON.

	Returns None if the file is not a DocType definition (e.g. Role, DocType State).
	"""
	if raw.get("doctype") != "DocType":
		return None
	name = raw.get("name")
	if not name:
		return None

	fields = [_strip_field(f) for f in raw.get("fields", []) if isinstance(f, dict)]
	# Drop layout fields that aren't useful for agents
	fields = [
		f for f in fields
		if f.get("fieldtype") not in {
			"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Fold",
		}
	]

	permissions = [_strip_permission(p) for p in raw.get("permissions", []) if isinstance(p, dict)]

	return {
		"name": name,
		"app": app,
		"module": raw.get("module", ""),
		"is_submittable": int(raw.get("is_submittable") or 0),
		"is_table": int(raw.get("istable") or 0),
		"is_tree": int(raw.get("is_tree") or 0),
		"is_single": int(raw.get("issingle") or 0),
		"autoname": raw.get("autoname", ""),
		"naming_rule": raw.get("naming_rule", ""),
		"track_changes": int(raw.get("track_changes") or 0),
		"title_field": raw.get("title_field", ""),
		"description": (raw.get("description") or "")[:300],
		"fields": fields,
		"permissions": permissions,
	}


# ── Extraction walker ──────────────────────────────────────────────


def _iter_doctype_json_files(app_path: Path):
	"""Yield every `<something>/doctype/<name>/<name>.json` under an app path."""
	# Frappe apps generally have doctypes at <app>/<module>/doctype/<name>/<name>.json
	# but layouts vary across apps - glob is the safest option.
	for json_file in app_path.rglob("doctype/*/*.json"):
		# Filter: file name must match parent directory name (Frappe convention)
		if json_file.stem == json_file.parent.name:
			yield json_file


def build_knowledge_graph(write: bool = True) -> dict[str, Any]:
	"""Walk every installed bench app and build the Framework KG.

	Args:
		write: If True, write the result to `alfred_client/data/framework_kg.json`.
			Set False for tests that want the dict without disk I/O.

	Returns:
		Dict keyed by DocType name. Values are the extracted records (see _extract_doctype).
	"""
	kg: dict[str, Any] = {}
	stats = {"apps_scanned": 0, "files_parsed": 0, "doctypes_extracted": 0, "parse_errors": 0}

	try:
		installed_apps = frappe.get_installed_apps()
	except Exception as e:
		logger.error("Could not list installed apps - KG build aborted: %s", e)
		return kg

	for app in installed_apps:
		try:
			app_path = Path(frappe.get_app_path(app))
		except Exception as e:
			logger.warning("Skipping app %s - get_app_path failed: %s", app, e)
			continue
		stats["apps_scanned"] += 1

		for json_file in _iter_doctype_json_files(app_path):
			stats["files_parsed"] += 1
			try:
				raw = json.loads(json_file.read_text())
			except Exception as e:
				stats["parse_errors"] += 1
				logger.debug("Failed to parse %s: %s", json_file, e)
				continue

			record = _extract_doctype(raw, app)
			if record is None:
				continue

			# If a DocType is defined in multiple apps (unusual), the last-write-wins.
			# We could prefer the most recently modified, but that's overkill for v1.
			kg[record["name"]] = record
			stats["doctypes_extracted"] += 1

	logger.info(
		"Framework KG built: %d apps, %d files parsed, %d doctypes, %d parse errors",
		stats["apps_scanned"], stats["files_parsed"],
		stats["doctypes_extracted"], stats["parse_errors"],
	)

	if write:
		data_dir = _data_dir()
		data_dir.mkdir(parents=True, exist_ok=True)
		kg_path = _kg_json_path()
		kg_path.write_text(json.dumps(kg, indent=1, sort_keys=True))
		logger.info("Framework KG written to %s (%d bytes)", kg_path, kg_path.stat().st_size)

	return kg


# ── In-memory cache with mtime-based invalidation ──────────────────


_KG_CACHE: dict[str, Any] = {"mtime": None, "data": None}
_PATTERNS_CACHE: dict[str, Any] = {"mtime": None, "data": None}


def _load_kg() -> dict[str, Any]:
	"""Return the framework KG dict. Builds on first call if file missing.

	Cached in-process keyed by file mtime so edits are picked up without
	restart but repeat calls are O(1) stat + dict lookup.
	"""
	path = _kg_json_path()
	if not path.exists():
		logger.info("Framework KG file missing - building on first access")
		build_knowledge_graph(write=True)
		if not path.exists():
			# Build may have produced an empty result - don't crash, just cache empty dict
			_KG_CACHE["data"] = {}
			_KG_CACHE["mtime"] = None
			return _KG_CACHE["data"]

	mtime = path.stat().st_mtime
	if _KG_CACHE["mtime"] != mtime:
		try:
			_KG_CACHE["data"] = json.loads(path.read_text())
			_KG_CACHE["mtime"] = mtime
			logger.debug("Framework KG loaded from disk (%d entries)", len(_KG_CACHE["data"]))
		except Exception as e:
			logger.error("Failed to load framework KG from %s: %s", path, e)
			_KG_CACHE["data"] = {}
	return _KG_CACHE["data"]


def _load_patterns() -> dict[str, Any]:
	"""Return the customization patterns dict. Loaded from YAML at first call.

	YAML parse uses `yaml.safe_load`. If pyyaml is missing (only a build-time
	dependency), we fall back to returning an empty dict and logging once.
	"""
	path = _patterns_yaml_path()
	if not path.exists():
		logger.warning("Customization patterns YAML missing at %s - returning empty", path)
		_PATTERNS_CACHE["data"] = {}
		return _PATTERNS_CACHE["data"]

	mtime = path.stat().st_mtime
	if _PATTERNS_CACHE["mtime"] != mtime:
		try:
			import yaml
		except ImportError:
			logger.error("pyyaml not installed - cannot load customization patterns")
			_PATTERNS_CACHE["data"] = {}
			_PATTERNS_CACHE["mtime"] = mtime
			return _PATTERNS_CACHE["data"]
		try:
			parsed = yaml.safe_load(path.read_text()) or {}
			if not isinstance(parsed, dict):
				logger.error(
					"customization_patterns.yaml root is %s, expected dict - ignoring",
					type(parsed).__name__,
				)
				parsed = {}
			_PATTERNS_CACHE["data"] = parsed
			_PATTERNS_CACHE["mtime"] = mtime
			logger.debug("Customization patterns loaded (%d entries)", len(parsed))
		except Exception as e:
			logger.error("Failed to load patterns from %s: %s", path, e)
			_PATTERNS_CACHE["data"] = {}
	return _PATTERNS_CACHE["data"]


# ── Public lookup + search API (called by MCP tools) ───────────────


def lookup_framework_doctype(doctype: str) -> dict[str, Any] | None:
	"""Return the extracted framework record for a DocType, or None if missing."""
	return _load_kg().get(doctype)


def list_framework_doctypes(app: str | None = None) -> list[dict[str, Any]]:
	"""Return a summary list of framework DocTypes, optionally filtered by app.

	Returns `[{name, app, module, is_submittable}]` - the subset agents need
	to pick candidates before drilling into a full record.
	"""
	kg = _load_kg()
	result = []
	for record in kg.values():
		if app and record.get("app") != app:
			continue
		result.append({
			"name": record.get("name"),
			"app": record.get("app"),
			"module": record.get("module"),
			"is_submittable": record.get("is_submittable", 0),
		})
	result.sort(key=lambda r: (r["app"] or "", r["name"] or ""))
	return result


def lookup_pattern(name: str) -> dict[str, Any] | None:
	"""Return one curated customization pattern by name, or None."""
	return _load_patterns().get(name)


def list_patterns(category: str | None = None) -> list[dict[str, Any]]:
	"""Return a summary list of curated patterns, optionally filtered by category."""
	patterns = _load_patterns()
	result = []
	for name, entry in patterns.items():
		if not isinstance(entry, dict):
			continue
		if category and entry.get("category") != category:
			continue
		result.append({
			"name": name,
			"description": entry.get("description", ""),
			"category": entry.get("category", ""),
			"when_to_use": entry.get("when_to_use", ""),
		})
	return result


def search_framework_knowledge(query: str, limit: int = 5) -> dict[str, Any]:
	"""Simple keyword search across the framework KG AND the pattern library.

	No embeddings, no BM25 - substring matching over hand-curated short strings.
	Returns top `limit` matches from each category. Good enough for v1.

	Match targets:
	  DocTypes: name + module
	  Patterns: name + description + keywords + when_to_use
	"""
	if not query:
		return {"doctypes": [], "patterns": []}

	query_lc = query.lower()
	query_terms = [t for t in query_lc.split() if len(t) >= 3]
	if not query_terms:
		return {"doctypes": [], "patterns": []}

	def _score(text: str) -> int:
		text_lc = (text or "").lower()
		return sum(1 for t in query_terms if t in text_lc)

	# Score doctypes
	doctype_hits: list[tuple[int, dict]] = []
	for record in _load_kg().values():
		haystack = f"{record.get('name', '')} {record.get('module', '')}"
		score = _score(haystack)
		if score > 0:
			doctype_hits.append((score, {
				"name": record.get("name"),
				"app": record.get("app"),
				"module": record.get("module"),
				"is_submittable": record.get("is_submittable", 0),
				"_score": score,
			}))
	doctype_hits.sort(key=lambda x: (-x[0], x[1]["name"] or ""))

	# Score patterns
	pattern_hits: list[tuple[int, dict]] = []
	for name, entry in _load_patterns().items():
		if not isinstance(entry, dict):
			continue
		haystack = " ".join([
			name,
			entry.get("description", ""),
			" ".join(entry.get("keywords", []) if isinstance(entry.get("keywords"), list) else []),
			entry.get("when_to_use", ""),
		])
		score = _score(haystack)
		if score > 0:
			pattern_hits.append((score, {
				"name": name,
				"description": entry.get("description", ""),
				"category": entry.get("category", ""),
				"when_to_use": entry.get("when_to_use", ""),
				"_score": score,
			}))
	pattern_hits.sort(key=lambda x: (-x[0], x[1]["name"]))

	return {
		"doctypes": [h[1] for h in doctype_hits[:limit]],
		"patterns": [h[1] for h in pattern_hits[:limit]],
	}


def clear_caches():
	"""Force reload on next access. Used by tests and after rebuilds."""
	_KG_CACHE["mtime"] = None
	_KG_CACHE["data"] = None
	_PATTERNS_CACHE["mtime"] = None
	_PATTERNS_CACHE["data"] = None
