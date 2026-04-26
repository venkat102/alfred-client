"""Runtime-error pre-flight checks for the dry-run pipeline.

These catch errors that don't surface during Frappe's insert-time
validation but explode at execution time:

  - Server Script: Python syntax via ``ast.parse`` + a deterministic
    "no imports" check (Frappe runs Server Scripts in a RestrictedPython
    sandbox with no ``__import__``, so an ``import json`` line compiles
    fine but blows up at runtime with a confusing error).
  - Notification: Jinja parse-only check via
    ``jinja2.Environment.from_string`` on subject/message/condition.
    We deliberately do NOT render: parse-time syntax errors are what
    we care about, rendering against a stub doc would just shake out
    missing-attribute noise. Using ``jinja2.Environment`` directly
    avoids ``frappe.render_template`` which catches and re-raises the
    Jinja2 exception as a Frappe ValidationError.
  - Client Script: loose balanced-brace / parens check (we don't have a
    JS parser at hand; the brace check catches the most common typo
    class).

Returns a list of human-readable issue strings (empty when all ok).
The caller in ``_routing.dry_run_changeset`` upgrades each entry to a
critical-severity issue.
"""

from __future__ import annotations


def _check_runtime_errors(doctype, data):
	"""Cheap pre-flight checks for errors that only surface at execution time.

	- Server Script: Python syntax via ast.parse + no-import walk
	- Notification: Jinja parse-only via jinja2.Environment.from_string
	- Client Script: loose regex check for balanced braces

	Returns a list of human-readable issue strings (empty if all ok).
	"""
	problems = []

	# --- Server Script: validate syntax + reject `import` ---
	# Frappe runs Server Scripts via frappe.utils.safe_exec.safe_exec, which
	# uses RestrictedPython + a `safe_globals` that has no `__import__`. That
	# means `import json` compiles fine at dry-run but blows up at runtime
	# with "name '__import__' is not defined" when the event fires.
	#
	# We catch this at dry-run via two complementary checks:
	#   1. ast.parse() for standard Python syntax errors
	#   2. ast.walk() looking for Import / ImportFrom nodes - deterministic
	#      and aligned with Frappe's "no imports in Server Scripts" contract
	if doctype == "Server Script":
		script = data.get("script", "")
		if script:
			import ast
			try:
				tree = ast.parse(script, filename="<alfred_dryrun>")
			except SyntaxError as e:
				problems.append(f"Server Script Python syntax error: {e.msg} at line {e.lineno}")
				tree = None
			except Exception as e:
				problems.append(f"Server Script compile failed: {e}")
				tree = None

			if tree is not None:
				import_lines = []
				for node in ast.walk(tree):
					if isinstance(node, (ast.Import, ast.ImportFrom)):
						# Render the offending line for a precise error message.
						if isinstance(node, ast.Import):
							names = ", ".join(alias.name for alias in node.names)
							rendered = f"import {names}"
						else:
							mod = node.module or ""
							names = ", ".join(alias.name for alias in node.names)
							rendered = f"from {mod} import {names}"
						import_lines.append(f"line {node.lineno}: `{rendered}`")
				if import_lines:
					problems.append(
						"Server Script uses `import` (not allowed in Frappe Server Scripts): "
						+ "; ".join(import_lines)
						+ ". Frappe runs Server Scripts in a restricted environment with no "
						"`__import__`. Use pre-bound names directly: `json.loads/dumps`, "
						"`datetime`, `frappe.*`, `frappe.utils.*`, `frappe.db.*`, and "
						"`frappe.make_get_request(url)` instead of `requests`."
					)

	# --- Notification: parse Jinja templates (don't render) ---
	#
	# IMPORTANT: do NOT use ``frappe.render_template`` here. Frappe's wrapper
	# catches ``jinja2.TemplateSyntaxError`` internally
	# (frappe/utils/jinja.py:render_template line 130, ``except TemplateError``)
	# and re-raises it as a Frappe ``ValidationError`` via ``frappe.throw``.
	# The Jinja2-typed exception never escapes the wrapper, so an
	# ``except TemplateSyntaxError`` clause here would never fire - the
	# broken template was silently swallowed by the trailing
	# ``except Exception`` and the error then surfaced from the savepoint
	# path with a different message format that test_deploy::Test 11
	# could not match.
	#
	# We only need to know if the template PARSES, not whether it renders -
	# parse-time syntax errors are exactly what this check is for. Using
	# ``jinja2.Environment().from_string(template)`` does pure parsing,
	# raises the unwrapped Jinja2 exception, and avoids the cost of
	# evaluating the template against a stub doc.
	if doctype == "Notification":
		from jinja2 import Environment, TemplateSyntaxError
		env = Environment()
		for field in ("subject", "message", "condition"):
			template = data.get(field)
			if not template or not isinstance(template, str):
				continue
			try:
				env.from_string(template)
			except TemplateSyntaxError as e:
				problems.append(f"Notification '{field}' Jinja syntax error: {e.message} at line {e.lineno}")
			except Exception:
				# Any other exception during PARSE is an unexpected jinja2
				# bug or environment issue - swallow it the same way the
				# original code did. Render-time errors (missing attribute
				# on a doc field, etc.) cannot occur here because we are
				# not rendering.
				pass

	# --- Client Script: loose balanced-brace check ---
	if doctype == "Client Script":
		script = data.get("script", "")
		if script:
			if script.count("{") != script.count("}"):
				problems.append("Client Script has unbalanced curly braces")
			if script.count("(") != script.count(")"):
				problems.append("Client Script has unbalanced parentheses")

	return problems
