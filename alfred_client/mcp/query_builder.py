"""Translate a validated QuerySpec into SQL via frappe.query_builder.

Security contract:
  - Every doctype referenced (from + joins) is checked with
    `frappe.has_permission(doctype, "read")`. Any miss rejects the whole
    query; we do NOT silently filter rows.
  - Row-level permission hooks registered under `permission_query_conditions`
    are called per doctype and their returned SQL fragments are ANDed into
    the WHERE clause. This preserves admin-written row-level rules.
  - A hardcoded `_BLOCKED_DOCTYPES` set rejects queries touching
    audit/auth/integration tables regardless of DocType permissions.
  - pypika/frappe.query_builder is structurally incapable of emitting
    non-SELECT SQL, so DDL/DML/multi-statement are out of scope by
    construction.
  - Query runs with `read_only=True` so it goes to the replica if one is
    configured. Output rows are still permission-scoped even without a
    replica.

The validated spec guarantees every referenced field resolves to a real
field on the referenced doctype; see `query_spec.validate_spec`.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.query_builder import Criterion, DocType, Order
from frappe.query_builder.functions import Avg, Count, Max, Min, Sum

from alfred_client.mcp.query_spec import FieldRef, QuerySpec, WhereClause

# Doctypes we refuse to query from the LLM-driven Insights surface even
# if the session user technically has read permission. Audit, auth, and
# integration tables can leak secondary secrets (tokens, request bodies,
# session fingerprints) that the LLM context should never surface.
_BLOCKED_DOCTYPES = frozenset({
	"Access Log",
	"Activity Log",
	"OAuth Bearer Token",
	"OAuth Authorization Code",
	"Integration Request",
	"Error Log",
	"Scheduled Job Log",
	"Token Cache",
})

_AGG_FN = {
	"count": Count,
	"sum": Sum,
	"avg": Avg,
	"min": Min,
	"max": Max,
}


def run_query_spec(spec: QuerySpec) -> dict:
	"""Build and execute a validated QuerySpec. Returns a dict with rows
	or an error marker; never raises on user-input problems.
	"""
	touched = _collect_doctypes(spec)

	for dt in touched:
		if dt in _BLOCKED_DOCTYPES:
			return {
				"error": "blocked_doctype",
				"doctype": dt,
				"message": (
					f"DocType {dt!r} is not queryable via Insights. Use Frappe "
					"Desk directly if you have the access you need."
				),
			}

	for dt in touched:
		if not frappe.has_permission(dt, "read"):
			return {
				"error": "permission_denied",
				"doctype": dt,
				"message": f"You do not have read permission on {dt!r}.",
			}

	tables = {dt: DocType(dt) for dt in touched}
	from_table = tables[spec.from_doctype]
	query = frappe.qb.from_(from_table)

	for j in spec.joins:
		joined = tables[j.to]
		local_expr = _field_expr(j.on_local, tables, spec.from_doctype)
		foreign_expr = _field_expr(j.on_foreign, tables, j.to, default_doctype=j.to)
		condition = local_expr == foreign_expr
		if j.type == "inner":
			query = query.inner_join(joined).on(condition)
		else:
			query = query.left_join(joined).on(condition)

	select_terms = []
	for s in spec.select:
		term = _select_term(s, tables, spec.from_doctype)
		if s.alias:
			term = term.as_(s.alias)
		select_terms.append(term)
	query = query.select(*select_terms)

	# Row-level permission hooks: call each registered function and AND
	# its SQL fragment into the WHERE clause. Hooks can return a string,
	# a list of strings, or a Criterion - handle all shapes.
	for dt in touched:
		for condition in _hook_conditions(dt):
			if condition is None:
				continue
			query = query.where(condition)

	for w in spec.where:
		query = query.where(_where_expr(w, tables, spec.from_doctype))

	if spec.group_by:
		query = query.groupby(*[
			_field_expr(g, tables, spec.from_doctype)
			for g in spec.group_by
		])

	if spec.having:
		for h in spec.having:
			query = query.having(_where_expr(h, tables, spec.from_doctype))

	for o in spec.order_by:
		# Order may reference a select alias, in which case we need a
		# CustomField / PseudoColumn. Easiest path: use a raw string
		# wrapped in pypika's Order mechanism.
		alias_match = next(
			(s for s in spec.select if s.alias and s.alias == o.field),
			None,
		)
		if alias_match is not None:
			from pypika.terms import PseudoColumn
			expr = PseudoColumn(alias_match.alias)
		else:
			expr = _field_expr(o.field, tables, spec.from_doctype)
		direction = Order.desc if o.dir == "desc" else Order.asc
		query = query.orderby(expr, order=direction)

	query = query.limit(spec.limit)

	try:
		rows = query.run(as_dict=True, read_only=True)
	except frappe.db.OperationalError as exc:
		return {"error": "query_failed", "message": str(exc)}
	except Exception as exc:
		return {"error": "query_failed", "message": str(exc)}

	rows = rows or []
	return {
		"rows": rows,
		"count": len(rows),
		"truncated": len(rows) == spec.limit,
		"doctypes": sorted(touched),
	}


def _collect_doctypes(spec: QuerySpec) -> list[str]:
	seen = [spec.from_doctype]
	for j in spec.joins:
		if j.to not in seen:
			seen.append(j.to)
	return seen


def _field_expr(name: str, tables: dict, from_doctype: str, default_doctype: str | None = None):
	"""Resolve 'field' or 'DocType.field' to a pypika Field."""
	if "." in name:
		doctype, field_name = name.split(".", 1)
	else:
		doctype = default_doctype or from_doctype
		field_name = name
	return tables[doctype][field_name]


def _select_term(s: FieldRef, tables: dict, from_doctype: str):
	expr = _field_expr(s.field, tables, from_doctype)
	if s.agg is None:
		return expr
	if s.agg == "count_distinct":
		return Count(expr).distinct()
	fn = _AGG_FN.get(s.agg)
	if fn is None:
		# Validator should prevent this, but fail safely if it ever slips.
		raise ValueError(f"Unknown aggregate {s.agg!r}")
	return fn(expr)


def _where_expr(w: WhereClause, tables: dict, from_doctype: str):
	expr = _field_expr(w.field, tables, from_doctype)
	op = w.op
	if op == "=":
		return expr == w.value
	if op == "!=":
		return expr != w.value
	if op == "<":
		return expr < w.value
	if op == "<=":
		return expr <= w.value
	if op == ">":
		return expr > w.value
	if op == ">=":
		return expr >= w.value
	if op == "like":
		return expr.like(w.value)
	if op == "not_like":
		return expr.not_like(w.value)
	if op == "in":
		return expr.isin(w.value)
	if op == "not_in":
		return expr.notin(w.value)
	if op == "is":
		return expr.isnull()
	if op == "is_not":
		return expr.isnotnull()
	raise ValueError(f"Unknown op {op!r}")


def _hook_conditions(doctype: str):
	"""Yield row-level permission conditions for the current user on `doctype`.

	Frappe registers `permission_query_conditions` as a hook keyed by
	DocType with a list of dotted Python paths. Each function is called
	with the user and returns an SQL fragment (string) that goes into the
	WHERE clause. Some hooks can also return a pypika Criterion. We wrap
	raw strings with `Criterion.wrap_raw` / `pypika.terms.CustomFunction`
	equivalent - here we use a simple pseudo-column literal.
	"""
	from pypika.terms import LiteralValue

	hooks = frappe.get_hooks("permission_query_conditions", {})
	# `hooks` is a dict[str, list[str]] (Frappe spreads list append hooks).
	paths = hooks.get(doctype, []) if isinstance(hooks, dict) else []
	if not paths:
		return

	user = frappe.session.user
	for path in paths:
		try:
			fn = frappe.get_attr(path)
		except Exception:
			continue
		try:
			result = fn(user)
		except TypeError:
			try:
				result = fn()
			except Exception:
				continue
		except Exception:
			continue

		if result is None:
			continue
		if isinstance(result, Criterion):
			yield result
		elif isinstance(result, str) and result.strip():
			# Wrap the raw SQL fragment so pypika emits it verbatim in
			# the WHERE clause. The fragment is produced by site-admin
			# Python code, not LLM output, so trust boundary is the same
			# as calling frappe.get_all() directly.
			yield LiteralValue(f"({result})")
		elif isinstance(result, list):
			for item in result:
				if isinstance(item, Criterion):
					yield item
				elif isinstance(item, str) and item.strip():
					yield LiteralValue(f"({item})")
