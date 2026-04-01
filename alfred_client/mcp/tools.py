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

	Safe for all users — returns names only, no schema details.
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
	# Use Frappe's built-in permission filtering — much faster than manual O(n) loop.
	# frappe.get_all already respects the current user's read permissions.
	permitted_doctypes = set(
		frappe.get_all("DocType", pluck="name", limit_page_length=0)
	)

	# Custom Fields — filtered
	custom_fields = frappe.get_all(
		"Custom Field",
		filters={"dt": ["in", list(permitted_doctypes)]},
		fields=["name", "dt", "fieldname", "fieldtype", "label"],
		limit_page_length=500,
	) if permitted_doctypes else []

	# Server Scripts — filtered
	server_scripts = frappe.get_all(
		"Server Script",
		filters={"reference_doctype": ["in", list(permitted_doctypes)]},
		fields=["name", "reference_doctype", "doctype_event", "script_type"],
		limit_page_length=200,
	) if permitted_doctypes else []

	# Client Scripts — filtered
	client_scripts = frappe.get_all(
		"Client Script",
		filters={"dt": ["in", list(permitted_doctypes)]},
		fields=["name", "dt", "view"],
		limit_page_length=200,
	) if permitted_doctypes else []

	# Workflows — filtered
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


# ── Tool Registry ────────────────────────────────────────────────

TOOL_REGISTRY = {
	"get_site_info": get_site_info,
	"get_doctypes": get_doctypes,
	"get_doctype_schema": get_doctype_schema,
	"get_existing_customizations": get_existing_customizations,
	"get_user_context": get_user_context,
	"check_permission": check_permission,
	"validate_name_available": validate_name_available,
	"has_active_workflow": has_active_workflow,
	"check_has_records": check_has_records,
}
