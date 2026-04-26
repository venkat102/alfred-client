"""Module-level constants for the deploy package.

Three groups:

  - ``FRAPPE_DEFAULT_FIELDS``: stdlib Frappe doc fields (name, owner,
    creation, etc.) that we exclude from rollback's restore-update so
    we don't try to write to read-only or auto-managed fields.
  - ``_DDL_TRIGGERING_DOCTYPES``: doctypes whose .insert() triggers
    DDL (CREATE TABLE / ALTER TABLE) directly or via controller side
    effects. MariaDB implicitly commits ALL pending DML before any DDL
    statement, which silently destroys a savepoint rollback - the
    intended "test insert" lands in the database for real and the user
    sees it as if they'd approved a deploy. We refuse to call .insert()
    on these and use meta-level validation instead.
  - ``_SAVEPOINT_SAFE_DOCTYPES``: doctypes whose .insert() is truly a
    DML-only row write with no schema side effects. We validate via
    savepoint-rollback because that catches more controller-level
    validators (uniqueness, format checks, workflow rules).

Re-exported from ``alfred_client.api.deploy`` (the package init) so
test_dry_run_safety.py's `from alfred_client.api.deploy import
_DDL_TRIGGERING_DOCTYPES` keeps working.
"""

from __future__ import annotations

# ── Frappe default fields to exclude from verification comparisons
FRAPPE_DEFAULT_FIELDS = {
	"name", "owner", "creation", "modified", "modified_by",
	"docstatus", "idx", "parent", "parenttype", "parentfield",
	"doctype", "amended_from", "_user_tags", "_comments",
	"_assign", "_liked_by", "_seen",
}


# Doctypes whose .insert() triggers DDL (CREATE TABLE / ALTER TABLE) either
# directly or via controller side effects. MariaDB implicitly commits ALL
# pending DML before any DDL statement, which silently destroys a savepoint
# rollback - the intended "test insert" lands in the database for real and
# the user sees it as if they'd approved a deploy. We refuse to call .insert()
# on these and use meta-level validation instead.
#
# - DocType:       CREATE TABLE
# - Custom Field:  ALTER TABLE ADD COLUMN
# - Property Setter: can trigger schema rebuilds for specific property changes
# - Workflow:      on_update() calls Custom Field.save() to add workflow_state
#                  to the target doctype, cascading into an ALTER TABLE
# - Workflow State: same cascade via workflow_state Link target
_DDL_TRIGGERING_DOCTYPES = frozenset({
	"DocType",
	"Custom Field",
	"Property Setter",
	"Workflow",
	"Workflow State",
	"Workflow Action Master",
	"DocField",
})

# Doctypes whose .insert() is truly a DML-only row write with no schema side
# effects. These we validate via savepoint-rollback because that catches more
# controller-level validators (uniqueness, format checks, workflow rules).
_SAVEPOINT_SAFE_DOCTYPES = frozenset({
	"Notification",
	"Server Script",
	"Client Script",
	"Print Format",
	"Letter Head",
	"Report",
	"Dashboard",
	"Dashboard Chart",
	"Role",
	"Custom DocPerm",
	"User Permission",
	"Translation",
	"Web Form",
	"Web Page",
})
