"""Structured query spec for Insights-mode aggregation and join queries.

The LLM emits a JSON spec describing the query shape (from / joins /
select / where / group_by / having / order_by / limit). This module
defines the dataclass schema and a strict validator. The translator in
`query_builder.py` is the only place that turns a validated spec into
SQL - it never sees user strings directly. The separation exists so the
validator's "reject unknown keys" rule has a clean home.

Validator contract:
  validate_spec(raw: dict) -> QuerySpec | {"error": str, "issues": [...]}

Never raises on invalid input; always returns a dict-shaped error so
the MCP tool wrapper can serialise it. Raises only on programmer error
(non-dict input type, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import frappe

_ALLOWED_AGG = frozenset({"count", "sum", "avg", "min", "max", "count_distinct"})
_ALLOWED_OPS = frozenset({
	"=", "!=", "<", "<=", ">", ">=",
	"like", "not_like",
	"in", "not_in",
	"is", "is_not",
})
_ALLOWED_DIR = frozenset({"asc", "desc"})
_ALLOWED_JOIN_TYPE = frozenset({"left", "inner"})

# Field reference: a bare name (resolved on from_doctype) or "DocType.field".
_FIELD_REF_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9 _-]*\.)?[a-z_][a-z0-9_]*$")
_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Frappe's standard fields live on every DocType's table and are valid
# targets for select / where / group_by / order_by even though they're
# not in the DocType's field metadata.
_STANDARD_FIELDS = frozenset({
	"name", "owner", "creation", "modified", "modified_by",
	"docstatus", "idx", "parent", "parenttype", "parentfield",
})

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50

_TOP_LEVEL_KEYS = frozenset({
	"from_doctype", "select", "joins", "where",
	"group_by", "having", "order_by", "limit",
})


@dataclass
class FieldRef:
	field: str
	agg: str | None = None
	alias: str | None = None


@dataclass
class JoinSpec:
	to: str
	type: str = "left"
	on_local: str = ""
	on_foreign: str = ""


@dataclass
class WhereClause:
	field: str
	op: str
	value: Any = None


@dataclass
class OrderSpec:
	field: str
	dir: str = "asc"


@dataclass
class QuerySpec:
	from_doctype: str
	select: list[FieldRef]
	joins: list[JoinSpec] = field(default_factory=list)
	where: list[WhereClause] = field(default_factory=list)
	group_by: list[str] = field(default_factory=list)
	having: list[WhereClause] = field(default_factory=list)
	order_by: list[OrderSpec] = field(default_factory=list)
	limit: int = _DEFAULT_LIMIT


def validate_spec(raw: Any) -> QuerySpec | dict:
	"""Validate a raw dict against the QuerySpec schema.

	Returns a fully constructed QuerySpec on success, or a dict
	``{"error": "invalid_spec", "issues": [...]}`` on failure. Never
	raises on user input; only raises for programmer errors (non-dict
	input type).
	"""
	if not isinstance(raw, dict):
		return _err(["spec must be a JSON object"])

	issues: list[str] = []

	unknown_keys = set(raw.keys()) - _TOP_LEVEL_KEYS
	if unknown_keys:
		issues.append(f"unknown keys: {sorted(unknown_keys)}")
		return _err(issues)

	from_doctype = raw.get("from_doctype")
	if not isinstance(from_doctype, str) or not from_doctype.strip():
		issues.append("from_doctype must be a non-empty string")
		return _err(issues)
	if not frappe.db.exists("DocType", from_doctype):
		issues.append(f"from_doctype {from_doctype!r} does not exist")
		return _err(issues)

	joins_raw = raw.get("joins") or []
	if not isinstance(joins_raw, list):
		return _err(["joins must be a list"])
	joins: list[JoinSpec] = []
	join_doctypes: list[str] = [from_doctype]
	for i, j in enumerate(joins_raw):
		j_issues = _parse_join(j, join_doctypes, i)
		if isinstance(j_issues, list):
			issues.extend(j_issues)
		else:
			joins.append(j_issues)
			join_doctypes.append(j_issues.to)

	all_doctypes = [from_doctype] + [j.to for j in joins]

	select_raw = raw.get("select")
	if not isinstance(select_raw, list) or not select_raw:
		issues.append("select must be a non-empty list")
		return _err(issues)
	select: list[FieldRef] = []
	for i, f in enumerate(select_raw):
		parsed = _parse_field_ref(f, all_doctypes, where_index=i, kind="select")
		if isinstance(parsed, list):
			issues.extend(parsed)
		else:
			select.append(parsed)

	where: list[WhereClause] = []
	for i, w in enumerate(raw.get("where") or []):
		parsed = _parse_where(w, all_doctypes, i, kind="where")
		if isinstance(parsed, list):
			issues.extend(parsed)
		else:
			where.append(parsed)

	having: list[WhereClause] = []
	for i, h in enumerate(raw.get("having") or []):
		parsed = _parse_where(h, all_doctypes, i, kind="having")
		if isinstance(parsed, list):
			issues.extend(parsed)
		else:
			having.append(parsed)

	group_by_raw = raw.get("group_by") or []
	if not isinstance(group_by_raw, list):
		issues.append("group_by must be a list of field strings")
	group_by: list[str] = []
	for g in group_by_raw:
		if not isinstance(g, str) or not _field_ref_ok(g, all_doctypes):
			issues.append(f"group_by entry {g!r} is not a valid field reference")
		else:
			group_by.append(g)

	order_by_raw = raw.get("order_by") or []
	if not isinstance(order_by_raw, list):
		issues.append("order_by must be a list")
	order_by: list[OrderSpec] = []
	for i, o in enumerate(order_by_raw):
		parsed = _parse_order(o, select, all_doctypes, i)
		if isinstance(parsed, list):
			issues.extend(parsed)
		else:
			order_by.append(parsed)

	limit = raw.get("limit", _DEFAULT_LIMIT)
	try:
		limit = int(limit)
	except (TypeError, ValueError):
		limit = _DEFAULT_LIMIT
	limit = max(1, min(limit, _MAX_LIMIT))

	if issues:
		return _err(issues)

	return QuerySpec(
		from_doctype=from_doctype,
		select=select,
		joins=joins,
		where=where,
		group_by=group_by,
		having=having,
		order_by=order_by,
		limit=limit,
	)


def _err(issues: list[str]) -> dict:
	return {"error": "invalid_spec", "issues": issues}


def _parse_field_ref(raw: Any, known_doctypes: list[str], where_index: int, kind: str) -> FieldRef | list[str]:
	if not isinstance(raw, dict):
		return [f"{kind}[{where_index}] must be an object"]
	unknown = set(raw.keys()) - {"field", "agg", "alias"}
	if unknown:
		return [f"{kind}[{where_index}] unknown keys: {sorted(unknown)}"]

	field_name = raw.get("field")
	if not isinstance(field_name, str) or not _field_ref_ok(field_name, known_doctypes):
		return [f"{kind}[{where_index}].field {field_name!r} is not a valid field reference"]

	agg = raw.get("agg")
	if agg is not None and agg not in _ALLOWED_AGG:
		return [f"{kind}[{where_index}].agg must be one of {sorted(_ALLOWED_AGG)} or null"]

	alias = raw.get("alias")
	if alias is not None:
		if not isinstance(alias, str) or not _ALIAS_RE.match(alias):
			return [f"{kind}[{where_index}].alias must match [A-Za-z_][A-Za-z0-9_]*"]

	return FieldRef(field=field_name, agg=agg, alias=alias)


def _parse_where(raw: Any, known_doctypes: list[str], index: int, kind: str) -> WhereClause | list[str]:
	if not isinstance(raw, dict):
		return [f"{kind}[{index}] must be an object"]
	unknown = set(raw.keys()) - {"field", "op", "value"}
	if unknown:
		return [f"{kind}[{index}] unknown keys: {sorted(unknown)}"]

	field_name = raw.get("field")
	if not isinstance(field_name, str) or not _field_ref_ok(field_name, known_doctypes):
		return [f"{kind}[{index}].field {field_name!r} is not a valid field reference"]

	op = raw.get("op")
	if op not in _ALLOWED_OPS:
		return [f"{kind}[{index}].op must be one of {sorted(_ALLOWED_OPS)}"]

	value = raw.get("value")
	if op in ("in", "not_in"):
		if not isinstance(value, list):
			return [f"{kind}[{index}] with op {op!r} requires list value"]
	elif op in ("is", "is_not"):
		if value not in ("null", None):
			return [f"{kind}[{index}] with op {op!r} requires value 'null' or null"]
		value = None

	return WhereClause(field=field_name, op=op, value=value)


def _parse_join(raw: Any, known_doctypes: list[str], index: int) -> JoinSpec | list[str]:
	if not isinstance(raw, dict):
		return [f"joins[{index}] must be an object"]
	unknown = set(raw.keys()) - {"to", "type", "on_local", "on_foreign"}
	if unknown:
		return [f"joins[{index}] unknown keys: {sorted(unknown)}"]

	to = raw.get("to")
	if not isinstance(to, str) or not to.strip():
		return [f"joins[{index}].to must be a non-empty string"]
	if not frappe.db.exists("DocType", to):
		return [f"joins[{index}].to {to!r} does not exist"]

	jtype = raw.get("type", "left")
	if jtype not in _ALLOWED_JOIN_TYPE:
		return [f"joins[{index}].type must be one of {sorted(_ALLOWED_JOIN_TYPE)}"]

	on_local = raw.get("on_local", "")
	on_foreign = raw.get("on_foreign", "")
	if not isinstance(on_local, str) or not _field_ref_ok(on_local, known_doctypes):
		return [f"joins[{index}].on_local {on_local!r} is not a valid field reference"]
	if not isinstance(on_foreign, str) or not _field_ref_ok(on_foreign, [to]):
		return [f"joins[{index}].on_foreign {on_foreign!r} is not a valid field on {to!r}"]

	return JoinSpec(to=to, type=jtype, on_local=on_local, on_foreign=on_foreign)


def _parse_order(raw: Any, select: list[FieldRef], known_doctypes: list[str], index: int) -> OrderSpec | list[str]:
	if not isinstance(raw, dict):
		return [f"order_by[{index}] must be an object"]
	unknown = set(raw.keys()) - {"field", "dir"}
	if unknown:
		return [f"order_by[{index}] unknown keys: {sorted(unknown)}"]

	field_name = raw.get("field")
	if not isinstance(field_name, str) or not field_name.strip():
		return [f"order_by[{index}].field must be a non-empty string"]

	# order_by may reference an alias from select OR a real field.
	aliases = {s.alias for s in select if s.alias}
	if field_name not in aliases and not _field_ref_ok(field_name, known_doctypes):
		return [f"order_by[{index}].field {field_name!r} is not a select alias or valid field reference"]

	direction = raw.get("dir", "asc")
	if direction not in _ALLOWED_DIR:
		return [f"order_by[{index}].dir must be one of {sorted(_ALLOWED_DIR)}"]

	return OrderSpec(field=field_name, dir=direction)


def _field_ref_ok(name: str, known_doctypes: list[str]) -> bool:
	"""True if `name` is shaped like 'field' or 'DocType.field' and
	resolves to a real field on the referenced doctype."""
	if not isinstance(name, str) or not _FIELD_REF_RE.match(name):
		return False
	if "." in name:
		doctype, field_name = name.split(".", 1)
		if doctype not in known_doctypes:
			return False
	else:
		field_name = name
		doctype = known_doctypes[0] if known_doctypes else None

	if field_name in _STANDARD_FIELDS:
		return True
	if doctype is None:
		return False
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return False
	return meta.get_field(field_name) is not None
