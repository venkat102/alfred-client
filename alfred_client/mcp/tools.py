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
	"dry_run_changeset": dry_run_changeset,
	# Consolidated tools from the Framework KG (Tier 1b)
	"lookup_doctype": lookup_doctype,
	"lookup_pattern": lookup_pattern,
	# Frappe Knowledge Base (platform rules, APIs, idioms)
	"lookup_frappe_knowledge": lookup_frappe_knowledge,
}
