"""Deployment engine for applying changesets to the Frappe site.

SECURITY: This is the most sensitive module in Alfred. It modifies the
customer's live site. Every operation:
  - Runs as the requesting user (frappe.set_user)
  - Uses ignore_permissions=False (Frappe enforces user permissions)
  - Is permission-verified server-side before execution
  - Is logged to Alfred Audit Log with before/after state

Called via: POST /api/method/alfred_client.api.deploy.apply_changeset

Package layout (TD-L1 split, mirrors alfred_processing's TD-H2 pipeline split):

  - ``_constants``          FRAPPE_DEFAULT_FIELDS + DDL/savepoint sets
  - ``_runtime_validation`` _check_runtime_errors (Python AST + Jinja + JS)
  - ``_semantic_checks``    per-doctype _check_* + _DOCTYPE_SPECIFIC_CHECKS
  - ``_routing``            dry_run_changeset + _dry_run_single
                            + _meta_check_only + _savepoint_dry_run
  - ``_deployment``         apply_changeset + verify_deployment + doc ops
  - ``_rollback``           rollback_changeset + _execute_rollback + audit log

Dependency graph (no cycles):

  _constants            (leaf)
  _runtime_validation   (leaf)
  _semantic_checks      (leaf)
  _routing              -> _constants, _runtime_validation, _semantic_checks
  _rollback             -> _constants
  _deployment           -> _rollback (for _write_audit_log)

CRITICAL contracts preserved:

  - ``alfred_client.api.deploy.apply_changeset`` - @frappe.whitelist
    routed dotted path. Vue (usePreviewActions.js, AlfredChatApp), tests
    (test_deploy.py), integrations.
  - ``alfred_client.api.deploy.rollback_changeset`` - @frappe.whitelist,
    same contract.
  - ``alfred_client.api.deploy.dry_run_changeset`` - imported by
    alfred_chat.py page module + mcp/tools.py + tests. Not whitelisted
    but the dotted path must keep resolving.
  - ``alfred_client.api.deploy._DDL_TRIGGERING_DOCTYPES`` /
    ``_SAVEPOINT_SAFE_DOCTYPES`` - test_dry_run_safety pins these.
  - ``alfred_client.api.deploy._dry_run_single`` - test_dry_run_safety
    imports it; the dispatch test (Test 3) monkeypatches the routing
    backends on ``alfred_client.api.deploy._routing`` (the submodule
    where they live), NOT on this package root.
"""

from __future__ import annotations

# ── Constants ─────────────────────────────────────────────────────────
from alfred_client.api.deploy._constants import (
	_DDL_TRIGGERING_DOCTYPES,
	_SAVEPOINT_SAFE_DOCTYPES,
	FRAPPE_DEFAULT_FIELDS,
)

# ── Runtime validation (Python AST, Jinja, JS) ────────────────────────
from alfred_client.api.deploy._runtime_validation import (
	_check_runtime_errors,
)

# ── Semantic checks (per-doctype) ─────────────────────────────────────
from alfred_client.api.deploy._semantic_checks import (
	_DOCTYPE_SPECIFIC_CHECKS,
	_check_client_script,
	_check_custom_field,
	_check_doctype,
	_check_notification,
	_check_server_script,
	_check_workflow,
)

# ── Dry-run routing + orchestrator ────────────────────────────────────
from alfred_client.api.deploy._routing import (
	_dry_run_single,
	_meta_check_only,
	_savepoint_dry_run,
	dry_run_changeset,
)

# ── Forward deploy + verification ─────────────────────────────────────
from alfred_client.api.deploy._deployment import (
	_acting_as,
	_create_document,
	_get_document_state,
	_update_document,
	apply_changeset,
	verify_deployment,
)

# ── Rollback + audit log ──────────────────────────────────────────────
from alfred_client.api.deploy._rollback import (
	_execute_rollback,
	_write_audit_log,
	rollback_changeset,
)

__all__ = [
	# constants
	"FRAPPE_DEFAULT_FIELDS",
	"_DDL_TRIGGERING_DOCTYPES",
	"_SAVEPOINT_SAFE_DOCTYPES",
	# runtime validation
	"_check_runtime_errors",
	# semantic checks
	"_DOCTYPE_SPECIFIC_CHECKS",
	"_check_client_script",
	"_check_custom_field",
	"_check_doctype",
	"_check_notification",
	"_check_server_script",
	"_check_workflow",
	# routing + orchestrator (dry_run is the public function used outside the package)
	"_dry_run_single",
	"_meta_check_only",
	"_savepoint_dry_run",
	"dry_run_changeset",
	# deploy
	"_acting_as",
	"_create_document",
	"_get_document_state",
	"_update_document",
	"apply_changeset",
	"verify_deployment",
	# rollback + audit
	"_execute_rollback",
	"_write_audit_log",
	"rollback_changeset",
]
