"""Frappe Knowledge Base - platform rules, APIs, and idioms.

A third knowledge layer, complementing:
  1. framework_kg.json        - vanilla DocType schemas (auto-extracted)
  2. customization_patterns.yaml - curated customization recipes

This module owns the third layer: hand-curated Frappe facts that agents
repeatedly get wrong from training memory alone. Things like "Server Scripts
cannot use import", "Notification uses Jinja with doc/user/frappe in scope",
"Workflow requires >=2 states" - platform-level constraints and conventions
that don't belong in either of the other two layers.

Entries live in data/frappe_kb/*.yaml, one file per kind:
  rules.yaml   - sandbox + operational constraints
  apis.yaml    - Frappe API reference (Phase C, auto-scraped + hand-overridden)
  idioms.yaml  - hooks, lifecycle, rename flows (Phase D)

Only rules.yaml is populated in Phase A. The loader tolerates missing files
(apis.yaml and idioms.yaml return empty dicts) so Phase A can ship before
Phase C/D are written.

Retrieval is keyword-only in Phase A; search_semantic/search_hybrid land in
Phase C when the embedding stack is wired.

Every exported function is side-effect-free beyond module-level caching so
this file is safe to import eagerly by MCP tool registration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import frappe

logger = logging.getLogger("alfred_client.frappe_kb")


# ── Schema ─────────────────────────────────────────────────────────

_KB_FILES = ("rules.yaml", "apis.yaml", "idioms.yaml", "style.yaml")
_VALID_KINDS = {"rule", "api", "idiom", "style"}
_REQUIRED_FIELDS = ("kind", "title", "summary", "keywords", "body", "verified_on")

# Keyword scoring weights - mirrors framework_kg.search_framework_knowledge
# so both KGs behave consistently for the agent. Title hits are the strongest
# signal, keywords are curated markers, body is incidental text.
_WEIGHT_TITLE = 5
_WEIGHT_KEYWORD = 3
_WEIGHT_APPLIES_TO = 4  # doctype targeting is nearly as strong as keyword
_WEIGHT_BODY = 1


# ── File locations ──────────────────────────────────────────────────


def _kb_dir() -> Path:
	"""Directory where the FKB YAMLs live. Resolved via frappe.get_app_path."""
	return Path(frappe.get_app_path("alfred_client")) / "data" / "frappe_kb"


# ── Cache (mtime-keyed, lazy, safe for in-process reload) ──────────


# One slot per kind so editing one file doesn't invalidate the others.
# `mtime` is the max mtime across the source files for that kind (today each
# kind is one file, but we structure the cache so it scales if we split later).
_CACHE: dict[str, dict[str, Any]] = {
	"rules.yaml": {"mtime": None, "data": None},
	"apis.yaml": {"mtime": None, "data": None},
	"idioms.yaml": {"mtime": None, "data": None},
	"style.yaml": {"mtime": None, "data": None},
}


def _load_file(filename: str) -> dict[str, Any]:
	"""Load one YAML file from the KB directory. Returns {} if missing or broken.

	The KB must gracefully handle missing apis.yaml / idioms.yaml during Phase A,
	where only rules.yaml ships. Parse errors log but don't raise - a broken
	knowledge layer must not take down agents that don't need it.
	"""
	path = _kb_dir() / filename
	if not path.exists():
		_CACHE[filename]["data"] = {}
		_CACHE[filename]["mtime"] = None
		return {}

	mtime = path.stat().st_mtime
	if _CACHE[filename]["mtime"] == mtime and _CACHE[filename]["data"] is not None:
		return _CACHE[filename]["data"]

	try:
		import yaml
	except ImportError:
		logger.error("pyyaml not installed - cannot load %s", filename)
		_CACHE[filename]["data"] = {}
		_CACHE[filename]["mtime"] = mtime
		return {}

	try:
		parsed = yaml.safe_load(path.read_text()) or {}
	except Exception as e:
		logger.error("Failed to parse %s: %s", filename, e)
		_CACHE[filename]["data"] = {}
		_CACHE[filename]["mtime"] = mtime
		return {}

	if not isinstance(parsed, dict):
		logger.error(
			"%s root is %s, expected dict - ignoring", filename, type(parsed).__name__
		)
		parsed = {}

	# Validate each entry; drop bad ones with a warning rather than failing
	# the whole file. This keeps the KB resilient to one-off typos.
	validated: dict[str, Any] = {}
	for entry_id, entry in parsed.items():
		if not isinstance(entry, dict):
			logger.warning("%s: entry %r is not a dict - skipping", filename, entry_id)
			continue
		missing = [f for f in _REQUIRED_FIELDS if f not in entry]
		if missing:
			logger.warning(
				"%s: entry %r missing required fields %s - skipping",
				filename, entry_id, missing,
			)
			continue
		if entry.get("kind") not in _VALID_KINDS:
			logger.warning(
				"%s: entry %r has invalid kind %r - skipping",
				filename, entry_id, entry.get("kind"),
			)
			continue
		if not isinstance(entry.get("keywords"), list):
			logger.warning(
				"%s: entry %r has non-list keywords - skipping", filename, entry_id,
			)
			continue
		validated[entry_id] = entry

	_CACHE[filename]["data"] = validated
	_CACHE[filename]["mtime"] = mtime
	logger.debug("Loaded %s: %d valid entries", filename, len(validated))
	return validated


def _load_all() -> dict[str, Any]:
	"""Return a single flat dict merging rules + apis + idioms.

	Entries are keyed by id (the YAML key). If two files accidentally share an
	id (they shouldn't), the later file wins - but log a warning so we can
	catch it during KB editing.
	"""
	merged: dict[str, Any] = {}
	for filename in _KB_FILES:
		entries = _load_file(filename)
		for entry_id, entry in entries.items():
			if entry_id in merged:
				logger.warning(
					"FKB entry id collision: %r appears in multiple files; last one wins",
					entry_id,
				)
			merged[entry_id] = dict(entry, id=entry_id)
	return merged


# ── Public API ─────────────────────────────────────────────────────


def load_kb() -> dict[str, Any]:
	"""Public accessor: return the merged dict of all FKB entries.

	Entries are keyed by id (the YAML key), with the id also written into each
	entry's dict so callers can pass the bare entry around without losing it.
	"""
	return _load_all()


def lookup_entry(entry_id: str) -> dict[str, Any] | None:
	"""Return one entry by id, or None if not found."""
	return _load_all().get(entry_id)


def list_entries(kind: str | None = None) -> list[dict[str, Any]]:
	"""Return summary list (id + title + summary + kind) for browsing.

	Filtering by kind lets agents pull just the rules or just the APIs without
	loading full bodies. Full entries are fetched via lookup_entry(id).
	"""
	result = []
	for entry_id, entry in _load_all().items():
		if kind and entry.get("kind") != kind:
			continue
		result.append({
			"id": entry_id,
			"kind": entry.get("kind"),
			"title": entry.get("title", ""),
			"summary": entry.get("summary", ""),
		})
	result.sort(key=lambda e: (e["kind"] or "", e["id"]))
	return result


def search_keyword(
	query: str,
	kind: str | None = None,
	k: int = 5,
	min_score: int = 3,
) -> list[dict[str, Any]]:
	"""Weighted keyword search across the FKB.

	Mirrors framework_kg.search_framework_knowledge's algorithm so the two
	KGs behave consistently:
	  - Split query into terms of length >= 3 (shorter terms are noise).
	  - Each term contributes at most once per (entry, field) pair, so a
	    single query word that appears in both title and body counts as
	    (title_weight + body_weight), not 2*title_weight.
	  - Rank by score desc, then by entry_id alpha for determinism.

	Weights:
	    title        = 5   (the entry's one-line headline)
	    keywords     = 3   (curated retrieval markers)
	    applies_to   = 4   (doctype filter - strong signal when present)
	    body         = 1   (incidental text; weak signal)

	Returns the top `k` entries whose score >= `min_score`. `min_score` exists
	so the auto-inject phase can require a non-trivial match before prepending
	the entry to the Developer task.
	"""
	if not query or not query.strip():
		return []

	query_lc = query.lower()
	terms = [t for t in query_lc.split() if len(t) >= 3]
	if not terms:
		return []

	def _field_score(text: str, weight: int) -> int:
		text_lc = (text or "").lower()
		return sum(weight for t in terms if t in text_lc)

	hits: list[tuple[int, str, dict[str, Any]]] = []
	for entry_id, entry in _load_all().items():
		if kind and entry.get("kind") != kind:
			continue

		title = entry.get("title", "")
		keywords_text = " ".join(
			entry.get("keywords", []) if isinstance(entry.get("keywords"), list) else []
		)
		applies_text = " ".join(
			entry.get("applies_to", []) if isinstance(entry.get("applies_to"), list) else []
		)
		body = entry.get("body", "")
		summary = entry.get("summary", "")

		score = (
			_field_score(title, _WEIGHT_TITLE)
			+ _field_score(keywords_text, _WEIGHT_KEYWORD)
			+ _field_score(applies_text, _WEIGHT_APPLIES_TO)
			+ _field_score(body, _WEIGHT_BODY)
			+ _field_score(summary, _WEIGHT_BODY)
		)
		if score < min_score:
			continue
		hits.append((score, entry_id, entry))

	hits.sort(key=lambda x: (-x[0], x[1]))
	return [
		dict(entry, id=entry_id, _score=score)
		for score, entry_id, entry in hits[:k]
	]


def clear_cache() -> None:
	"""Force reload on next access. Used by tests and after file edits."""
	for slot in _CACHE.values():
		slot["mtime"] = None
		slot["data"] = None
