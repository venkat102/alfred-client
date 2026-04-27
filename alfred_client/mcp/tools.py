"""MCP tool implementations for the Alfred Client App.

All tools run within the Frappe user's session context (frappe.set_user is
called at WebSocket handshake time). Frappe's permission system is automatically
enforced for all frappe.get_all(), frappe.has_permission(), etc. calls.

Tool tiers:
  Tier 1 (Reference): Safe for all logged-in users
  Tier 2 (Schema): Requires read permission on target DocTypes
  Tier 3 (Validation): For Tester Agent dry-run checks
"""

import json

import frappe


def _safe_execute(func):
	"""Wrap a tool function to catch exceptions and return structured errors."""
	def wrapper(*args, **kwargs):
		try:
			return func(*args, **kwargs)
		except frappe.PermissionError as e:
			return {"error": "permission_denied", "message": str(e)}
		except frappe.DoesNotExistError as e:
			return {"error": "not_found", "message": str(e)}
		except Exception as e:
			return {"error": "internal_error", "message": str(e)}
	wrapper.__name__ = func.__name__
	wrapper.__doc__ = func.__doc__
	return wrapper


# ── Tier 1: Reference Tools (all logged-in users) ────────────────

@_safe_execute
def get_site_info():
	"""Get basic site information: Frappe version, installed apps, default company, country."""
	import importlib

	import frappe.utils

	apps = []
	for app in frappe.get_installed_apps():
		try:
			mod = importlib.import_module(app)
			version = getattr(mod, "__version__", "unknown")
		except Exception:
			version = "unknown"
		apps.append({"name": app, "version": str(version)})

	company = frappe.db.get_default("company") or ""
	country = frappe.db.get_default("country") or ""

	return {
		"frappe_version": str(frappe.__version__),
		"installed_apps": apps,
		"default_company": company,
		"country": country,
		"site_url": frappe.utils.get_url(),
	}


@_safe_execute
def get_doctypes(module=None):
	"""List DocType names and modules. Optionally filter by module.

	Safe for all users - returns names only, no schema details.
	Supports optional module filter to reduce payload on large sites.
	"""
	filters = {"istable": 0}
	if module:
		filters["module"] = module

	doctypes = frappe.get_all(
		"DocType",
		filters=filters,
		fields=["name", "module"],
		order_by="name",
		limit_page_length=500,
	)
	return {"doctypes": doctypes, "count": len(doctypes)}


# ── Tier 2: Schema Tools (requires read permission) ──────────────

@_safe_execute
def get_doctype_schema(doctype):
	"""Get full field schema for a DocType. Requires read permission.

	Returns field definitions, permissions, naming rule, and module.
	"""
	if not frappe.has_permission(doctype, "read"):
		return {
			"error": "permission_denied",
			"message": f"You do not have read permission on {doctype}",
		}

	meta = frappe.get_meta(doctype)
	fields = []
	for f in meta.fields:
		fields.append({
			"fieldname": f.fieldname,
			"fieldtype": f.fieldtype,
			"label": f.label,
			"options": f.options,
			"reqd": f.reqd,
			"default": f.default,
			"in_list_view": f.in_list_view,
			"read_only": f.read_only,
			"hidden": f.hidden,
			"depends_on": f.depends_on,
			"description": f.description,
		})

	permissions = []
	for p in meta.permissions:
		permissions.append({
			"role": p.role,
			"read": p.read,
			"write": p.write,
			"create": p.create,
			"delete": p.delete,
			"submit": p.submit,
		})

	return {
		"doctype": doctype,
		"module": meta.module,
		"naming_rule": meta.autoname or "",
		"is_submittable": meta.is_submittable,
		"is_single": meta.issingle,
		"is_child_table": meta.istable,
		"fields": fields,
		"permissions": permissions,
		"field_count": len(fields),
	}


@_safe_execute
def get_existing_customizations():
	"""List existing customizations filtered by user's read permissions.

	Returns custom fields, server scripts, client scripts, and workflows
	only for DocTypes the current user can access.
	"""
	# Use Frappe's built-in permission filtering - much faster than manual O(n) loop.
	# frappe.get_all already respects the current user's read permissions.
	permitted_doctypes = set(
		frappe.get_all("DocType", pluck="name", limit_page_length=0)
	)

	# Custom Fields - filtered
	custom_fields = frappe.get_all(
		"Custom Field",
		filters={"dt": ["in", list(permitted_doctypes)]},
		fields=["name", "dt", "fieldname", "fieldtype", "label"],
		limit_page_length=500,
	) if permitted_doctypes else []

	# Server Scripts - filtered
	server_scripts = frappe.get_all(
		"Server Script",
		filters={"reference_doctype": ["in", list(permitted_doctypes)]},
		fields=["name", "reference_doctype", "doctype_event", "script_type"],
		limit_page_length=200,
	) if permitted_doctypes else []

	# Client Scripts - filtered
	client_scripts = frappe.get_all(
		"Client Script",
		filters={"dt": ["in", list(permitted_doctypes)]},
		fields=["name", "dt", "view"],
		limit_page_length=200,
	) if permitted_doctypes else []

	# Workflows - filtered
	workflows = frappe.get_all(
		"Workflow",
		filters={"document_type": ["in", list(permitted_doctypes)], "is_active": 1},
		fields=["name", "document_type", "workflow_state_field"],
		limit_page_length=100,
	) if permitted_doctypes else []

	return {
		"custom_fields": custom_fields,
		"server_scripts": server_scripts,
		"client_scripts": client_scripts,
		"workflows": workflows,
	}


# Truncation budgets. Server Scripts carry real logic so they get a bigger
# window than Client Scripts (mostly event wiring) and Notification subjects
# (one-liner strings). Keeping previews small keeps the injected SITE STATE
# banner under a few KB even for heavily-customized DocTypes.
_SITE_DETAIL_SCRIPT_PREVIEW = 600       # chars of Server Script body
_SITE_DETAIL_CLIENT_PREVIEW = 300       # chars of Client Script body
_SITE_DETAIL_NOTIF_SUBJECT = 120        # chars of Notification subject


def _truncate(s: str | None, limit: int) -> str:
	s = s or ""
	if len(s) <= limit:
		return s
	return s[:limit].rstrip() + "..."


@_safe_execute
def get_site_customization_detail(doctype):
	"""Deep per-DocType recon of existing site customizations.

	Where `get_existing_customizations()` lists names + event types site-wide,
	this returns the full body and structure for artefacts on ONE DocType -
	so an agent asked to "add a validation to Employee" can see the existing
	validation Server Script and decide whether to extend, replace, or refuse.

	Returns (on success):
	  {
	    "doctype": str,
	    "custom_fields":  [{fieldname, fieldtype, label, options, reqd}, ...],
	    "server_scripts": [{name, script_type, doctype_event, script,
	                         disabled}, ...],      # script truncated at 600
	    "workflows":      [{name, is_active, workflow_state_field,
	                         states:[{state, doc_status, allow_edit}, ...],
	                         transitions:[{state, action, next_state,
	                                       allowed}, ...]}, ...],
	    "notifications":  [{name, event, channel, subject, enabled}, ...],
	    "client_scripts": [{name, view, script_preview, enabled}, ...],
	  }

	Returns `{"error": "not_found", ...}` if the DocType doesn't exist on the
	site, `{"error": "permission_denied", ...}` if the caller lacks read
	permission. The _safe_execute wrapper handles both.

	Reads respect Frappe's permission system: frappe.get_all already filters
	by the caller's DocType-level read permission. We additionally gate the
	call on `frappe.has_permission(doctype, "read")` to fail fast (and with a
	clear error) for DocTypes the user can't see.
	"""
	if not doctype or not isinstance(doctype, str):
		return {"error": "invalid_argument", "message": "doctype must be a non-empty string"}

	# DocType existence check. frappe.db.exists returns the name (truthy) or None.
	if not frappe.db.exists("DocType", doctype):
		return {"error": "not_found", "message": f"DocType {doctype!r} not found"}

	# Permission check on the parent DocType - same pattern the existing
	# customization list uses, just scoped to one target here.
	if not frappe.has_permission(doctype, "read"):
		return {"error": "permission_denied",
				"message": f"No read permission on {doctype!r}"}

	# Custom Fields on this DocType.
	custom_fields = frappe.get_all(
		"Custom Field",
		filters={"dt": doctype},
		fields=["name", "fieldname", "fieldtype", "label", "options", "reqd"],
		order_by="idx asc",
		limit_page_length=100,
	)

	# Server Scripts wired to this DocType. Include the body (truncated)
	# because that's the whole point of this tool - the agent needs to see
	# what logic is already there.
	server_scripts_raw = frappe.get_all(
		"Server Script",
		filters={"reference_doctype": doctype},
		fields=["name", "script_type", "doctype_event", "disabled", "script"],
		limit_page_length=50,
	)
	server_scripts = [
		{
			"name": s.get("name"),
			"script_type": s.get("script_type"),
			"doctype_event": s.get("doctype_event"),
			"disabled": int(s.get("disabled") or 0),
			"script": _truncate(s.get("script"), _SITE_DETAIL_SCRIPT_PREVIEW),
		}
		for s in server_scripts_raw
	]

	# Workflows. States and transitions are child tables on the Workflow doc,
	# so we load the full doc (not frappe.get_all) to walk them.
	workflow_names = frappe.get_all(
		"Workflow",
		filters={"document_type": doctype},
		pluck="name",
		limit_page_length=20,
	)
	workflows = []
	for wf_name in workflow_names:
		try:
			wf = frappe.get_doc("Workflow", wf_name)
		except Exception:
			continue
		workflows.append({
			"name": wf.name,
			"is_active": int(getattr(wf, "is_active", 0) or 0),
			"workflow_state_field": getattr(wf, "workflow_state_field", None),
			"states": [
				{
					"state": st.state,
					"doc_status": str(st.doc_status) if st.doc_status is not None else "0",
					"allow_edit": st.allow_edit,
				}
				for st in (wf.states or [])
			],
			"transitions": [
				{
					"state": tr.state,
					"action": tr.action,
					"next_state": tr.next_state,
					"allowed": tr.allowed,
				}
				for tr in (wf.transitions or [])
			],
		})

	# Notifications targeting this DocType.
	notifications = frappe.get_all(
		"Notification",
		filters={"document_type": doctype},
		fields=["name", "event", "channel", "subject", "enabled"],
		limit_page_length=50,
	)
	for n in notifications:
		n["subject"] = _truncate(n.get("subject"), _SITE_DETAIL_NOTIF_SUBJECT)
		n["enabled"] = int(n.get("enabled") or 0)

	# Client Scripts.
	client_scripts_raw = frappe.get_all(
		"Client Script",
		filters={"dt": doctype},
		fields=["name", "view", "enabled", "script"],
		limit_page_length=50,
	)
	client_scripts = [
		{
			"name": c.get("name"),
			"view": c.get("view"),
			"enabled": int(c.get("enabled") or 0),
			"script_preview": _truncate(c.get("script"), _SITE_DETAIL_CLIENT_PREVIEW),
		}
		for c in client_scripts_raw
	]

	return {
		"doctype": doctype,
		"custom_fields": custom_fields,
		"server_scripts": server_scripts,
		"workflows": workflows,
		"notifications": notifications,
		"client_scripts": client_scripts,
	}


@_safe_execute
def get_user_context():
	"""Get the current user's email, roles, permissions, and permitted modules."""
	user = frappe.session.user
	roles = frappe.get_roles(user)

	# Get permitted modules
	permitted_modules = []
	all_modules = frappe.get_all("Module Def", pluck="name")
	for mod in all_modules:
		try:
			if frappe.has_permission("Module Def", doc=mod):
				permitted_modules.append(mod)
		except Exception:
			permitted_modules.append(mod)  # Default to accessible if check fails

	return {
		"user": user,
		"roles": roles,
		"permitted_modules": permitted_modules,
	}


# ── Tier 3: Validation Tools (for Tester Agent dry-run) ──────────

@_safe_execute
def check_permission(doctype, action="read"):
	"""Deterministic permission check. Returns {permitted: true/false}.

	Actions: read, write, create, delete, submit, cancel, amend
	"""
	valid_actions = {"read", "write", "create", "delete", "submit", "cancel", "amend"}
	if action not in valid_actions:
		return {"error": "invalid_action", "message": f"Action must be one of: {', '.join(sorted(valid_actions))}"}

	permitted = frappe.has_permission(doctype, action)
	return {
		"doctype": doctype,
		"action": action,
		"permitted": bool(permitted),
		"user": frappe.session.user,
	}


@_safe_execute
def validate_name_available(doctype, name):
	"""Check if a document name is already taken.

	Used to detect naming conflicts before creating new documents.
	"""
	exists = frappe.db.exists(doctype, name)
	return {
		"doctype": doctype,
		"name": name,
		"available": not bool(exists),
		"exists": bool(exists),
	}


@_safe_execute
def has_active_workflow(doctype):
	"""Check if a DocType already has an active workflow.

	Frappe allows only one active workflow per DocType.
	"""
	workflows = frappe.get_all(
		"Workflow",
		filters={"document_type": doctype, "is_active": 1},
		fields=["name", "workflow_state_field"],
	)
	return {
		"doctype": doctype,
		"has_active_workflow": len(workflows) > 0,
		"workflows": workflows,
	}


@_safe_execute
def check_has_records(doctype):
	"""Check if a DocType has existing data records.

	Used before rollback or deletion to avoid data loss.
	Requires read permission on the DocType.
	"""
	if not frappe.has_permission(doctype, "read"):
		return {
			"error": "permission_denied",
			"message": f"You do not have read permission on {doctype}",
		}

	try:
		count = frappe.db.count(doctype)
	except Exception:
		count = 0

	return {
		"doctype": doctype,
		"has_records": count > 0,
		"count": count,
	}


_GET_LIST_MAX_ROWS = 500


@_safe_execute
def get_list(
	doctype,
	filters=None,
	fields=None,
	limit=50,
	order_by=None,
):
	"""Read records from a DocType, respecting the session user's permissions.

	Used by Insights mode to answer simple data questions like
	"list of active customers" or "count of pending invoices". Frappe's
	permission layer runs automatically via ignore_permissions=False, so
	a user without read access gets an empty list rather than a leak.

	Args:
		doctype: DocType name, e.g. "Customer".
		filters: dict (``{"disabled": 0}``) or list-of-triples
			(``[["modified", ">=", "2026-01-01"]]``). Raw SQL strings are
			rejected. None means "no filter".
		fields: list of field names to return. Unknown fields are dropped
			with a warning rather than raising. Defaults to ``["name"]``.
		limit: max rows. Clamped to ``[1, 500]``.
		order_by: optional Frappe order-by clause, e.g. ``"modified desc"``.

	Returns:
		``{"doctype": ..., "rows": [...], "count": N, "truncated": bool,
		   "fields": [...], "dropped_fields": [...]}``
	"""
	if not isinstance(doctype, str) or not doctype.strip():
		return {"error": "invalid_argument", "message": "doctype must be a non-empty string"}

	if not frappe.db.exists("DocType", doctype):
		return {"error": "unknown_doctype", "message": f"DocType {doctype!r} not found"}

	if isinstance(filters, str):
		return {
			"error": "invalid_filters",
			"message": "Raw SQL filter strings are not accepted. Pass a dict or list of triples.",
		}
	if filters is not None and not isinstance(filters, (dict, list)):
		return {
			"error": "invalid_filters",
			"message": "filters must be a dict, list of triples, or None.",
		}

	try:
		limit = int(limit)
	except (TypeError, ValueError):
		limit = 50
	limit = max(1, min(limit, _GET_LIST_MAX_ROWS))

	meta = frappe.get_meta(doctype)
	standard_fields = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}

	requested = fields if fields else ["name"]
	if not isinstance(requested, list):
		return {"error": "invalid_argument", "message": "fields must be a list of strings or None"}

	resolved_fields = []
	dropped_fields = []
	for f in requested:
		if not isinstance(f, str) or not f.strip():
			dropped_fields.append(f)
			continue
		if f in standard_fields or meta.get_field(f) is not None:
			resolved_fields.append(f)
		else:
			dropped_fields.append(f)
	if not resolved_fields:
		resolved_fields = ["name"]

	try:
		rows = frappe.get_list(
			doctype,
			filters=filters or None,
			fields=resolved_fields,
			limit_page_length=limit,
			order_by=order_by or None,
			ignore_permissions=False,
		)
	except frappe.PermissionError as e:
		return {"error": "permission_denied", "message": str(e)}

	return {
		"doctype": doctype,
		"rows": rows,
		"count": len(rows),
		"truncated": len(rows) == limit,
		"fields": resolved_fields,
		"dropped_fields": dropped_fields,
	}


@_safe_execute
def run_query(spec):
	"""Run a structured aggregation / join query against the live site.

	Wraps :mod:`alfred_client.mcp.query_spec` + :mod:`alfred_client.mcp.query_builder`
	so Insights mode can answer aggregation questions without seeing SQL.
	The LLM emits a JSON spec; this tool validates it, checks
	per-doctype read permissions, injects row-level permission hooks,
	blocks a sensitive-table set, and executes via frappe.query_builder
	(structurally SELECT-only).

	Args:
		spec: dict or JSON string. See query_spec.QuerySpec.

	Returns:
		``{"rows": [...], "count": N, "truncated": bool, "doctypes": [...]}``
		on success, or a structured error dict
		(``invalid_spec`` / ``blocked_doctype`` / ``permission_denied`` /
		``query_failed``).
	"""
	from alfred_client.mcp.query_builder import run_query_spec
	from alfred_client.mcp.query_spec import validate_spec

	if isinstance(spec, str):
		try:
			spec = json.loads(spec)
		except (ValueError, TypeError) as exc:
			return {
				"error": "invalid_spec",
				"issues": [f"spec is not valid JSON: {exc}"],
			}

	validated = validate_spec(spec)
	if isinstance(validated, dict):
		return validated  # already an error dict from the validator
	return run_query_spec(validated)


# ── Tier 3: Validation Tools (Tester Agent / Pipeline) ───────────

@_safe_execute
def dry_run_changeset(changes):
	"""Dry-run a changeset via savepoint rollback.

	Returns {valid, issues, validated}. Does NOT commit to the database.
	Also validates Server Script Python syntax, Notification Jinja templates,
	and Client Script balanced-brace checks inside the savepoint window.

	Use this before presenting a final changeset to the user so they only see
	validated solutions.
	"""
	from alfred_client.api.deploy import dry_run_changeset as _dry_run
	if isinstance(changes, str):
		changes = json.loads(changes)
	return _dry_run(changes)


# ── Tier 1b: Framework Knowledge Graph (consolidated tools) ──────
#
# The KG layer separates vanilla framework facts (from bench app JSONs) from
# live site state (from get_doctype_schema). These two consolidated tools
# replace get_doctypes + get_doctype_schema + the originally planned
# get_framework_doctype / list_framework_doctypes / get_customization_pattern
# / list_customization_patterns / search_framework_knowledge. Fewer tools with
# richer semantics = lower agent cognitive load (SWE-Agent ACI principle).


@_safe_execute
def lookup_doctype(name, layer="both"):
	"""Look up a DocType across the framework KG and/or the live site.

	Args:
		name: DocType name (e.g. "Sales Order").
		layer: "framework" returns vanilla facts from the bench app JSONs.
			"site" returns the current live schema (includes custom fields).
			"both" (default) returns a merged view with custom fields flagged.

	Example:
		lookup_doctype("Sales Order", layer="framework")
		  -> {"name": "Sales Order", "is_submittable": 1, "fields": [...], ...}
		lookup_doctype("Sales Order", layer="both")
		  -> {"name": ..., "framework": {...}, "site": {...}, "custom_fields": [...]}
	"""
	from alfred_client.mcp import framework_kg

	layer = (layer or "both").lower()
	if layer not in {"framework", "site", "both"}:
		return {"error": "invalid_argument", "message": f"layer must be framework|site|both, got {layer!r}"}

	framework_record = None
	if layer in {"framework", "both"}:
		framework_record = framework_kg.lookup_framework_doctype(name)
		if layer == "framework":
			if framework_record is None:
				return {
					"error": "not_found",
					"message": f"DocType {name!r} not found in the framework KG. Try lookup_doctype(name, layer='site') to check the live site.",
				}
			return framework_record

	# layer == "site" or "both"
	site_record = get_doctype_schema(name)  # reuses existing Tier 2 tool + permission check
	if isinstance(site_record, dict) and site_record.get("error"):
		# If site layer failed but we have framework data (in "both" mode), return that
		if layer == "both" and framework_record is not None:
			return {
				"name": name,
				"framework": framework_record,
				"site": site_record,
				"custom_fields": [],
			}
		return site_record

	if layer == "site":
		return site_record

	# layer == "both" - merge
	framework_fieldnames = set()
	if framework_record:
		framework_fieldnames = {
			f.get("fieldname") for f in framework_record.get("fields", [])
			if isinstance(f, dict) and f.get("fieldname")
		}

	site_fields = site_record.get("fields", []) if isinstance(site_record, dict) else []
	custom_fields = [
		f for f in site_fields
		if isinstance(f, dict) and f.get("fieldname") not in framework_fieldnames
	]

	return {
		"name": name,
		"framework": framework_record,
		"site": site_record,
		"custom_fields": custom_fields,
	}


@_safe_execute
def lookup_pattern(query, kind="all"):
	"""Look up a customization pattern from the curated library.

	Args:
		query: Pattern name or keyword(s) depending on kind.
		kind: "name" - exact match by pattern name.
			"search" - keyword search across names + descriptions + keywords.
			"list" - return all pattern summaries (query ignored).
			"all" (default) - try exact name first, fall back to search.

	Example:
		lookup_pattern("approval_notification", kind="name")
		  -> {"pattern": {...curated template...}}
		lookup_pattern("email approver on leave", kind="search")
		  -> {"doctypes": [...], "patterns": [{"name": "approval_notification", ...}]}
		lookup_pattern("", kind="list")
		  -> {"patterns": [{"name": "...", "description": "...", "when_to_use": "..."}]}
	"""
	from alfred_client.mcp import framework_kg

	kind = (kind or "all").lower()
	if kind not in {"name", "search", "list", "all"}:
		return {"error": "invalid_argument", "message": f"kind must be name|search|list|all, got {kind!r}"}

	if kind == "list":
		return {"patterns": framework_kg.list_patterns()}

	if kind == "name":
		entry = framework_kg.lookup_pattern(query)
		if entry is None:
			return {"error": "not_found", "message": f"Pattern {query!r} not found"}
		return {"pattern": entry, "name": query}

	if kind == "search":
		return framework_kg.search_framework_knowledge(query)

	# kind == "all"
	entry = framework_kg.lookup_pattern(query)
	if entry is not None:
		return {"pattern": entry, "name": query, "source": "exact"}
	search = framework_kg.search_framework_knowledge(query)
	return {"source": "search", **search}


@_safe_execute
def lookup_frappe_knowledge(query, kind=None, k=3):
	"""Retrieve Frappe platform knowledge (rules, APIs, idioms) from the FKB.

	Third knowledge layer alongside framework_kg (DocType schemas) and
	customization_patterns (recipes). Holds platform rules (e.g. "Server
	Scripts can't use import"), Frappe API reference, and Frappe idioms
	(hooks, lifecycle, rename flows). Call when you need to check a platform
	constraint before writing code, or to look up how a Frappe API behaves.

	The pipeline auto-injects the top matches for the user's enhanced prompt,
	so you usually don't need to call this manually - but it's here for when
	you want to pull additional context during Thought/Action reasoning.

	Args:
		query: Free text (e.g. "server script import", "db.get_value",
			"workflow states"). Short keywords work well.
		kind: Optional filter - "rule" | "api" | "idiom" | None (all).
		k: Number of top matches to return (default 3).

	Example:
		lookup_frappe_knowledge("server script import")
		  -> {"entries": [{id: "server_script_no_imports", title: ..., body: ..., _score: 16}, ...]}
		lookup_frappe_knowledge("how to send an alert", kind="rule")
		  -> {"entries": [{id: "notification_doctype_vs_server_script", ...}]}
		lookup_frappe_knowledge("", kind="rule")  # discovery
		  -> {"entries": [... summaries of all rule entries ...]}

	Returns:
		{"entries": [...]} - top-k matches with full body + examples, or
		summary list if query is empty.
		{"error": "...", "message": "..."} on invalid kind.
	"""
	from alfred_client.mcp import frappe_kb

	if kind is not None:
		kind_norm = (kind or "").lower()
		if kind_norm not in {"rule", "api", "idiom", "style"}:
			return {
				"error": "invalid_argument",
				"message": f"kind must be rule|api|idiom|style, got {kind!r}",
			}
	else:
		kind_norm = None

	if not query or not str(query).strip():
		# Discovery mode: return summaries so the agent can pick one by id.
		return {"entries": frappe_kb.list_entries(kind=kind_norm), "mode": "list"}

	results = frappe_kb.search_keyword(str(query), kind=kind_norm, k=int(k or 3))
	return {"entries": results, "mode": "search", "query": query}


# ── Tier 2: Schema Grounding (richer meta for the Developer agent) ──
#
# These tools exist because the Developer agent's dominant failure mode is
# "right shape, wrong details" - it picks the right primitive (Custom Field
# vs Property Setter vs DocPerm) but hallucinates fieldnames, types,
# permlevels, parent paths. The fix is grounding: feed the agent real
# get_meta() output so it chooses fieldnames from a known list rather than
# inventing them. All four tools read live site state via Frappe's own
# meta cache - no second cache layer.

# Frappe's allowed permlevels in practice: documented as 0-9.
_VALID_PERMLEVELS = set(range(10))

# Frappe core fieldtypes. Sourced from frappe/core/doctype/docfield/docfield.json
# fieldtype Select options. Used to flag obvious "Tex" / "Strng" typos.
_VALID_FIELDTYPES = {
	"Attach", "Attach Image", "Autocomplete", "Barcode", "Button", "Check",
	"Code", "Color", "Column Break", "Currency", "Data", "Date", "Datetime",
	"Duration", "Dynamic Link", "Float", "Fold", "Geolocation",
	"Heading", "HTML", "HTML Editor", "Icon", "Image", "Int", "JSON",
	"Link", "Long Text", "Markdown Editor", "Password",
	"Percent", "Phone", "Read Only", "Rating", "Section Break", "Select",
	"Signature", "Small Text", "Tab Break", "Table", "Table MultiSelect",
	"Text", "Text Editor", "Time",
}


def _field_to_dict(f, source: str) -> dict:
	"""Serialize a meta DocField row, tagging where it came from."""
	return {
		"fieldname": f.fieldname,
		"fieldtype": f.fieldtype,
		"label": f.label or "",
		"options": f.options or "",
		"reqd": int(f.reqd or 0),
		"permlevel": int(f.permlevel or 0),
		"in_list_view": int(f.in_list_view or 0),
		"read_only": int(f.read_only or 0),
		"hidden": int(f.hidden or 0),
		"depends_on": f.depends_on or "",
		"mandatory_depends_on": getattr(f, "mandatory_depends_on", "") or "",
		"insert_after": getattr(f, "insert_after", None),
		"source": source,
	}


@_safe_execute
def get_doctype_context(doctype):
	"""Layered meta the Developer agent needs before generating a field-touching
	changeset. Reuses frappe.get_meta() so DocField + Custom Field + Property
	Setter layering is correct - no reimplementation.

	Returns standard fields, custom fields, property setters, workflow (if any),
	and one-hop linked DocTypes. Each field carries a `source` tag
	(standard/custom/property_setter).

	Errors: invalid_argument, not_found, permission_denied (read-permission gate).
	"""
	if not doctype or not isinstance(doctype, str):
		return {"error": "invalid_argument", "message": "doctype must be a non-empty string"}

	if not frappe.db.exists("DocType", doctype):
		return {"error": "not_found", "message": f"DocType {doctype!r} not found"}

	if not frappe.has_permission(doctype, "read"):
		return {"error": "permission_denied",
				"message": f"No read permission on {doctype!r}"}

	meta = frappe.get_meta(doctype)

	# Build a set of fieldnames from the bench-shipped DocType JSON so we can
	# tag fields that arrived via Custom Field. frappe.get_meta merges DocField
	# + Custom Field rows into a single field list, but doesn't preserve origin.
	# Query the underlying tables to recover the source.
	standard_fieldnames = {
		row["fieldname"] for row in frappe.get_all(
			"DocField",
			filters={"parent": doctype, "parenttype": "DocType"},
			fields=["fieldname"],
			limit_page_length=0,
		) if row.get("fieldname")
	}
	custom_fieldnames = {
		row["fieldname"] for row in frappe.get_all(
			"Custom Field",
			filters={"dt": doctype},
			fields=["fieldname"],
			limit_page_length=0,
		) if row.get("fieldname")
	}

	fields = []
	custom_field_count = 0
	for f in meta.fields:
		if not f.fieldname:
			continue
		if f.fieldname in custom_fieldnames:
			source = "custom"
			custom_field_count += 1
		elif f.fieldname in standard_fieldnames:
			source = "standard"
		else:
			# Field exists in meta but not in either source table - typically
			# a virtual field added via property setter or run-time injection.
			source = "property_setter"
		fields.append(_field_to_dict(f, source))

	# Property Setters (overrides on this DocType - not new fields, but property
	# changes like "make field X reqd=1"). Surfacing these helps the agent see
	# why a field's behaviour differs from the framework default.
	property_setters = frappe.get_all(
		"Property Setter",
		filters={"doc_type": doctype},
		fields=["property", "value", "field_name"],
		limit_page_length=200,
	)

	# Workflow (Frappe allows only one active per DocType, but list all to flag
	# inactive ones the agent might otherwise propose to recreate).
	workflow_names = frappe.get_all(
		"Workflow",
		filters={"document_type": doctype},
		pluck="name",
		limit_page_length=5,
	)
	workflow = None
	if workflow_names:
		try:
			wf = frappe.get_doc("Workflow", workflow_names[0])
			workflow = {
				"name": wf.name,
				"is_active": int(getattr(wf, "is_active", 0) or 0),
				"workflow_state_field": getattr(wf, "workflow_state_field", None),
				"states": [
					{"state": st.state,
					 "doc_status": str(st.doc_status) if st.doc_status is not None else "0",
					 "allow_edit": st.allow_edit}
					for st in (wf.states or [])
				],
				"transitions": [
					{"state": tr.state, "action": tr.action,
					 "next_state": tr.next_state, "allowed": tr.allowed}
					for tr in (wf.transitions or [])
				],
			}
		except Exception:
			workflow = None

	# One-hop linked DocTypes (Link + Table fieldtypes). The agent uses this
	# to know "if I touch the customer field on Sales Order, the link target
	# is Customer".
	linked_doctypes = []
	for f in meta.fields:
		if f.fieldtype in {"Link", "Table", "Table MultiSelect", "Dynamic Link"} and f.options:
			linked_doctypes.append({"fieldname": f.fieldname, "options": f.options})

	return {
		"doctype": doctype,
		"module": meta.module,
		"is_submittable": int(meta.is_submittable or 0),
		"is_single": int(meta.issingle or 0),
		"naming_rule": meta.autoname or "",
		"fields": fields,
		"property_setters": property_setters,
		"workflow": workflow,
		"linked_doctypes": linked_doctypes,
		"field_count": len(fields),
		"custom_field_count": custom_field_count,
	}


@_safe_execute
def get_doctype_perms(doctype):
	"""DocPerm + Custom DocPerm matrix for one DocType, plus permlevels in use.

	Returns the role x perm matrix the Developer needs when the prompt
	mentions roles or permlevels (e.g. "only HR Manager can write notes").

	Errors: invalid_argument, not_found, permission_denied.
	"""
	if not doctype or not isinstance(doctype, str):
		return {"error": "invalid_argument", "message": "doctype must be a non-empty string"}

	if not frappe.db.exists("DocType", doctype):
		return {"error": "not_found", "message": f"DocType {doctype!r} not found"}

	if not frappe.has_permission(doctype, "read"):
		return {"error": "permission_denied",
				"message": f"No read permission on {doctype!r}"}

	# Standard DocPerms ship with the DocType JSON; Custom DocPerms are
	# site-local overrides. Both feed Frappe's permission system at runtime.
	perm_fields = [
		"role", "permlevel", "read", "write", "create", "delete",
		"submit", "cancel", "amend", "report", "export", "import_",
		"share", "print", "email", "if_owner",
	]

	def _row(p, source):
		out = {"role": p.get("role"), "permlevel": int(p.get("permlevel") or 0),
			   "source": source}
		# Coerce all flag columns to int 0/1 - some come back as bool, some as None.
		for col in ("read", "write", "create", "delete", "submit", "cancel",
					"amend", "report", "export", "share", "print", "email",
					"if_owner"):
			out[col] = int(p.get(col) or 0)
		# DB column is `import_` (Python keyword clash); expose as `import` to agents.
		out["import"] = int(p.get("import_") or 0)
		return out

	std_rows = frappe.get_all(
		"DocPerm",
		filters={"parent": doctype, "parenttype": "DocType"},
		fields=perm_fields,
		limit_page_length=0,
	)
	custom_rows = frappe.get_all(
		"Custom DocPerm",
		filters={"parent": doctype, "parenttype": "DocType"},
		fields=perm_fields,
		limit_page_length=0,
	)

	perms = [_row(p, "standard") for p in std_rows] + \
			[_row(p, "custom") for p in custom_rows]

	permlevels_in_use = sorted({p["permlevel"] for p in perms})

	# Permlevels declared on actual fields - these are the ones an agent can
	# legitimately reference in a new DocPerm row. A permlevel that no field
	# uses is essentially dead.
	meta = frappe.get_meta(doctype)
	field_permlevels = sorted({int(f.permlevel or 0) for f in meta.fields})

	return {
		"doctype": doctype,
		"perms": perms,
		"permlevels_in_use": permlevels_in_use,
		"valid_permlevels_for_fields": field_permlevels,
	}


@_safe_execute
def find_field(doctype, fieldname_hint, top_k=3):
	"""Fuzzy-match a hinted fieldname against live meta. Deterministic;
	difflib-based; no LLM, no embeddings. Includes child-table fields.

	Returns:
	  {"doctype": str, "hint": str,
	   "exact_match": {fieldname, fieldtype, label, source} | None,
	   "candidates": [{fieldname, fieldtype, label, source, confidence}]}

	Errors: invalid_argument, not_found, permission_denied.
	"""
	import difflib

	if not doctype or not isinstance(doctype, str):
		return {"error": "invalid_argument", "message": "doctype must be a non-empty string"}
	if not fieldname_hint or not isinstance(fieldname_hint, str):
		return {"error": "invalid_argument", "message": "fieldname_hint must be a non-empty string"}

	if not frappe.db.exists("DocType", doctype):
		return {"error": "not_found", "message": f"DocType {doctype!r} not found"}
	if not frappe.has_permission(doctype, "read"):
		return {"error": "permission_denied",
				"message": f"No read permission on {doctype!r}"}

	try:
		top_k = max(1, min(int(top_k or 3), 10))
	except (TypeError, ValueError):
		top_k = 3

	meta = frappe.get_meta(doctype)
	custom_fieldnames = {
		row["fieldname"] for row in frappe.get_all(
			"Custom Field", filters={"dt": doctype},
			fields=["fieldname"], limit_page_length=0,
		) if row.get("fieldname")
	}

	# Walk fields on this doctype + every Table/Table MultiSelect child target
	# so a hint like "qty" resolves on Sales Order Item, not just Sales Order.
	candidates = []
	def _push(f, parent_doctype):
		source = "custom" if f.fieldname in custom_fieldnames else "standard"
		candidates.append({
			"fieldname": f.fieldname,
			"fieldtype": f.fieldtype,
			"label": f.label or "",
			"source": source,
			"_parent": parent_doctype,
		})

	for f in meta.fields:
		if not f.fieldname:
			continue
		_push(f, doctype)
		if f.fieldtype in {"Table", "Table MultiSelect"} and f.options:
			try:
				if frappe.db.exists("DocType", f.options):
					child_meta = frappe.get_meta(f.options)
					for cf in child_meta.fields:
						if cf.fieldname:
							_push(cf, f.options)
			except Exception:
				continue

	hint_norm = fieldname_hint.strip().lower().replace("-", "_").replace(" ", "_")

	# Exact match wins regardless of fuzzy ranking.
	exact_match = None
	for c in candidates:
		if c["fieldname"].lower() == hint_norm:
			exact_match = {k: v for k, v in c.items() if not k.startswith("_")}
			break

	# Fuzzy ranking: difflib ratio against fieldname + label tokens.
	scored = []
	for c in candidates:
		fn_ratio = difflib.SequenceMatcher(None, hint_norm, c["fieldname"].lower()).ratio()
		lbl_norm = c["label"].lower().replace(" ", "_") if c["label"] else ""
		lbl_ratio = difflib.SequenceMatcher(None, hint_norm, lbl_norm).ratio() if lbl_norm else 0.0
		conf = max(fn_ratio, lbl_ratio)
		if conf > 0.4:  # cheap relevance floor
			out = {k: v for k, v in c.items() if not k.startswith("_")}
			out["confidence"] = round(conf, 3)
			scored.append(out)

	scored.sort(key=lambda x: x["confidence"], reverse=True)
	return {
		"doctype": doctype,
		"hint": fieldname_hint,
		"exact_match": exact_match,
		"candidates": scored[:top_k],
	}


def _validate_one_change(item: dict, item_index: int, issues: list) -> None:
	"""Append issues found in a single changeset entry to ``issues``.

	Static checks only - no DB writes, no transaction. Catches the four
	hallucination classes the Developer hits: unknown_field, duplicate_field,
	invalid_permlevel, unknown_parent_doctype, missing_mandatory,
	bad_link_target, fieldtype_options_mismatch.
	"""
	op = item.get("op") or item.get("operation") or ""
	doctype = item.get("doctype") or ""
	data = item.get("data") or {}

	def _add(code: str, message: str, severity: str = "critical",
			 fix_hint: str | None = None) -> None:
		issues.append({
			"severity": severity,
			"item_index": item_index,
			"doctype": doctype,
			"code": code,
			"message": message,
			"fix_hint": fix_hint,
		})

	# Custom Field: parent + fieldname collision check
	if doctype == "Custom Field" and op == "create":
		parent = data.get("dt")
		fieldname = data.get("fieldname")
		fieldtype = data.get("fieldtype")
		if not parent:
			_add("missing_mandatory", "Custom Field 'data.dt' is required",
				 fix_hint="Set data.dt to the target DocType name.")
			return
		if not frappe.db.exists("DocType", parent):
			_add("unknown_parent_doctype",
				 f"Parent DocType {parent!r} does not exist on this site",
				 fix_hint="Use a real DocType name. Try get_doctypes() to browse.")
			return
		if not fieldname:
			_add("missing_mandatory", "Custom Field 'data.fieldname' is required")
			return
		if fieldtype and fieldtype not in _VALID_FIELDTYPES:
			_add("fieldtype_options_mismatch",
				 f"fieldtype {fieldtype!r} is not a valid Frappe field type",
				 fix_hint=f"Pick from: Data, Link, Select, Int, Float, Check, Date, Datetime, Text, ...")
		# Check for collision with existing field (standard or custom).
		try:
			parent_meta = frappe.get_meta(parent)
			if parent_meta.get_field(fieldname) is not None:
				_add("duplicate_field",
					 f"Field {fieldname!r} already exists on {parent!r} - cannot create a duplicate",
					 fix_hint=f"Pick a different fieldname (e.g. custom_{fieldname}) or update the existing field instead.")
		except Exception:
			pass
		# Link/Table fieldtypes need a valid options DocType.
		if fieldtype in {"Link", "Table", "Table MultiSelect", "Dynamic Link"}:
			options = data.get("options")
			if not options:
				_add("missing_mandatory",
					 f"fieldtype {fieldtype!r} requires data.options (target DocType)")
			elif fieldtype != "Dynamic Link" and not frappe.db.exists("DocType", options):
				_add("bad_link_target",
					 f"Link target DocType {options!r} does not exist")
		# Select fieldtype needs non-empty options.
		if fieldtype == "Select":
			options = data.get("options")
			if not options:
				_add("missing_mandatory",
					 "Select fieldtype requires data.options (newline-separated values)")
		return

	# Property Setter: doc_type + field_name validity
	if doctype == "Property Setter" and op == "create":
		target = data.get("doc_type")
		field_name = data.get("field_name")
		if not target:
			_add("missing_mandatory", "Property Setter 'data.doc_type' is required")
			return
		if not frappe.db.exists("DocType", target):
			_add("unknown_parent_doctype",
				 f"Property Setter target {target!r} does not exist")
			return
		# field_name may be empty for DocType-level properties (e.g. naming_rule).
		if field_name:
			try:
				if frappe.get_meta(target).get_field(field_name) is None:
					_add("unknown_field",
						 f"Property Setter references field {field_name!r} which does not exist on {target!r}",
						 fix_hint=f"Call find_field({target!r}, {field_name!r}) for likely matches.")
			except Exception:
				pass
		return

	# DocPerm / Custom DocPerm: parent + permlevel + role
	if doctype in {"DocPerm", "Custom DocPerm"} and op == "create":
		parent = data.get("parent")
		permlevel = data.get("permlevel", 0)
		role = data.get("role")
		if not parent:
			_add("missing_mandatory", f"{doctype} 'data.parent' is required")
			return
		if not frappe.db.exists("DocType", parent):
			_add("unknown_parent_doctype",
				 f"{doctype} parent {parent!r} does not exist")
			return
		try:
			permlevel_int = int(permlevel)
			if permlevel_int not in _VALID_PERMLEVELS:
				_add("invalid_permlevel",
					 f"permlevel {permlevel!r} is out of range (valid: 0-9)",
					 fix_hint="Frappe convention reserves permlevel 0-9 for grouping field-level perms.")
		except (TypeError, ValueError):
			_add("invalid_permlevel",
				 f"permlevel {permlevel!r} must be an integer 0-9")
		if role and not frappe.db.exists("Role", role):
			_add("bad_link_target",
				 f"Role {role!r} does not exist on this site",
				 fix_hint="Use a real Role name (System Manager, Sales User, ...).")
		return

	# DocType create: fields list, naming
	if doctype == "DocType" and op == "create":
		name = data.get("name")
		fields = data.get("fields") or []
		if not name:
			_add("missing_mandatory", "DocType 'data.name' is required")
		if not isinstance(fields, list):
			_add("missing_mandatory",
				 "DocType 'data.fields' must be a list of field definitions")
			return
		seen = set()
		for fi, f in enumerate(fields):
			if not isinstance(f, dict):
				continue
			fn = f.get("fieldname")
			ft = f.get("fieldtype")
			if fn:
				if fn in seen:
					_add("duplicate_field",
						 f"DocType {name!r} declares field {fn!r} twice (fields[{fi}])")
				seen.add(fn)
			if ft and ft not in _VALID_FIELDTYPES:
				_add("fieldtype_options_mismatch",
					 f"DocType {name!r} field[{fi}] fieldtype {ft!r} is not a valid Frappe field type")
		return

	# All other ops/doctypes: nothing static we can confidently flag without
	# a savepoint. Defer to dry_run_changeset.


@_safe_execute
def validate_changeset(changeset):
	"""Static schema validation BEFORE the savepoint dry-run. Catches the
	classes the Developer hallucinates: unknown fields, type mismatches,
	duplicate fields, invalid permlevels, bad parent DocType paths, missing
	mandatory fields per primitive. No DB writes; no transaction.

	Complementary to (not replacing) dry_run_changeset which uses real
	savepoint rollback. Cheap enough to run on every retry.

	Returns:
	  {"valid": bool, "issues": [...], "checked": int}
	"""
	if isinstance(changeset, str):
		try:
			changeset = json.loads(changeset)
		except (ValueError, TypeError) as exc:
			return {
				"valid": False,
				"issues": [{
					"severity": "critical", "item_index": -1, "doctype": "",
					"code": "invalid_json",
					"message": f"changeset string is not valid JSON: {exc}",
					"fix_hint": "Pass a JSON array or a list of dict items.",
				}],
				"checked": 0,
			}
	if not isinstance(changeset, list):
		return {
			"valid": False,
			"issues": [{
				"severity": "critical", "item_index": -1, "doctype": "",
				"code": "invalid_argument",
				"message": "changeset must be a list of changeset items",
				"fix_hint": None,
			}],
			"checked": 0,
		}

	issues: list = []
	checked = 0
	for i, item in enumerate(changeset):
		if not isinstance(item, dict):
			issues.append({
				"severity": "critical", "item_index": i, "doctype": "",
				"code": "invalid_argument",
				"message": f"changeset[{i}] is not a dict",
				"fix_hint": None,
			})
			continue
		_validate_one_change(item, i, issues)
		checked += 1

	# Critical issues fail the changeset; warnings alone do not.
	has_critical = any(iss.get("severity") == "critical" for iss in issues)
	return {"valid": not has_critical, "issues": issues, "checked": checked}


# ── Tool Registry ────────────────────────────────────────────────

TOOL_REGISTRY = {
	"get_site_info": get_site_info,
	"get_doctypes": get_doctypes,
	"get_doctype_schema": get_doctype_schema,  # kept for backwards-compat; prefer lookup_doctype
	"get_existing_customizations": get_existing_customizations,
	"get_site_customization_detail": get_site_customization_detail,
	"get_user_context": get_user_context,
	"check_permission": check_permission,
	"validate_name_available": validate_name_available,
	"has_active_workflow": has_active_workflow,
	"check_has_records": check_has_records,
	"get_list": get_list,
	"run_query": run_query,
	"dry_run_changeset": dry_run_changeset,
	# Consolidated tools from the Framework KG (Tier 1b)
	"lookup_doctype": lookup_doctype,
	"lookup_pattern": lookup_pattern,
	# Frappe Knowledge Base (platform rules, APIs, idioms)
	"lookup_frappe_knowledge": lookup_frappe_knowledge,
	# Schema grounding (Tier 2) - feed real meta to the Developer agent
	"get_doctype_context": get_doctype_context,
	"get_doctype_perms": get_doctype_perms,
	"find_field": find_field,
	"validate_changeset": validate_changeset,
}
