import frappe


def has_app_permission():
	"""Check if current user has permission to access the Alfred app.
	Used by the apps screen to control visibility."""
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
