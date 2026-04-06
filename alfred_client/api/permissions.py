import frappe


@frappe.whitelist()
def has_app_permission():
	"""Check if current user has permission to access the Alfred app.
	Used by the apps screen and page load to control visibility."""
	try:
		validate_alfred_access()
		return True
	except frappe.PermissionError:
		return False


def validate_alfred_access():
	"""Gate access to Alfred features based on allowed_roles in Alfred Settings.

	Reads the allowed_roles child table from Alfred Settings. If the table is empty,
	defaults to allowing System Manager and any role with create permission on Custom Field.
	Raises frappe.PermissionError if the current user has none of the allowed roles.

	Called by every Alfred UI page and API endpoint.
	"""
	if frappe.session.user == "Administrator":
		return

	user_roles = set(frappe.get_roles())

	try:
		settings = frappe.get_single("Alfred Settings")
		allowed_roles = [row.role for row in settings.allowed_roles]
	except Exception:
		# Alfred Settings may not exist yet (fresh install before migrate)
		allowed_roles = []

	if not allowed_roles:
		# Default: System Manager + roles with create permission on Custom Field
		allowed_roles = ["System Manager"]
		custom_field_roles = frappe.get_all(
			"Custom DocPerm",
			filters={"parent": "Custom Field", "create": 1},
			pluck="role",
		)
		allowed_roles.extend(custom_field_roles)

	if not user_roles.intersection(set(allowed_roles)):
		frappe.throw(
			frappe._("You do not have permission to access Alfred. Contact your administrator."),
			frappe.PermissionError,
		)


# ── Alfred Conversation Permissions ────────────────────────────────

def conversation_has_permission(doc, ptype="read", user=None):
	"""Owner can do anything. Others need Frappe sharing or System Manager role."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	if doc.user == user:
		return True
	if "System Manager" in frappe.get_roles(user):
		return True
	# Check frappe.share (DocShare table)
	shared = frappe.share.get_sharing_permissions("Alfred Conversation", doc.name, user)
	return shared.get("read") if ptype == "read" else shared.get("write")


def conversation_query_conditions(user=None):
	"""SQL filter: own conversations + shared ones."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""

	shared_names = frappe.get_all(
		"DocShare",
		filters={"share_doctype": "Alfred Conversation", "user": user},
		pluck="share_name",
	)

	conditions = [f"`tabAlfred Conversation`.user = {frappe.db.escape(user)}"]
	if shared_names:
		escaped = ", ".join(frappe.db.escape(n) for n in shared_names)
		conditions.append(f"`tabAlfred Conversation`.name IN ({escaped})")

	return f"({' OR '.join(conditions)})"


# ── Alfred Message Permissions ─────────────────────────────────────

def message_has_permission(doc, ptype="read", user=None):
	"""Delegate to the parent conversation's permission."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True
	conv_user = frappe.db.get_value("Alfred Conversation", doc.conversation, "user")
	if conv_user == user:
		return True
	shared = frappe.share.get_sharing_permissions("Alfred Conversation", doc.conversation, user)
	return shared.get("read") if ptype == "read" else shared.get("write")


def message_query_conditions(user=None):
	"""Only show messages from conversations the user can access."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""

	shared_convs = frappe.get_all(
		"DocShare",
		filters={"share_doctype": "Alfred Conversation", "user": user},
		pluck="share_name",
	)

	conditions = [
		f"""`tabAlfred Message`.conversation IN (
			SELECT name FROM `tabAlfred Conversation` WHERE user = {frappe.db.escape(user)}
		)"""
	]
	if shared_convs:
		escaped = ", ".join(frappe.db.escape(n) for n in shared_convs)
		conditions.append(f"`tabAlfred Message`.conversation IN ({escaped})")

	return f"({' OR '.join(conditions)})"


# ── Alfred Changeset Permissions ───────────────────────────────────

def changeset_has_permission(doc, ptype="read", user=None):
	"""Delegate to the parent conversation's permission."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True
	conv_user = frappe.db.get_value("Alfred Conversation", doc.conversation, "user")
	if conv_user == user:
		return True
	shared = frappe.share.get_sharing_permissions("Alfred Conversation", doc.conversation, user)
	return shared.get("read") if ptype == "read" else shared.get("write")


def changeset_query_conditions(user=None):
	"""Only show changesets from conversations the user can access."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""

	shared_convs = frappe.get_all(
		"DocShare",
		filters={"share_doctype": "Alfred Conversation", "user": user},
		pluck="share_name",
	)

	conditions = [
		f"""`tabAlfred Changeset`.conversation IN (
			SELECT name FROM `tabAlfred Conversation` WHERE user = {frappe.db.escape(user)}
		)"""
	]
	if shared_convs:
		escaped = ", ".join(frappe.db.escape(n) for n in shared_convs)
		conditions.append(f"`tabAlfred Changeset`.conversation IN ({escaped})")

	return f"({' OR '.join(conditions)})"
