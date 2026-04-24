"""Live end-to-end FKB smoke.

Creates a conversation, sends a validation prompt, polls for the resulting
changeset, and prints a structured summary showing:

  - whether Dev mode was selected
  - whether a changeset was produced
  - the shape of the changeset (Server Script? Custom Field? DocType?)
  - whether the generated Server Script contains any `import` statements
  - dry-run validity

Run via:
    bench --site dev.alfred execute alfred_client.smoke_fkb_tmp.run

Cleans up its own conversation on success unless KEEP_SMOKE_CONV=1.
"""

import json
import os
import time

import frappe


PROMPT = (
	"Add a validation on the Employee DocType that throws "
	"\"Employee must be at least 24 years old\" when date_of_birth "
	"makes the employee younger than 24."
)

# How long to wait for the pipeline to produce a changeset.
POLL_TIMEOUT_SEC = 1500  # 25 minutes; full pipeline with 32B model is ~15-25 min
POLL_INTERVAL_SEC = 10


def _banner(title: str):
	print()
	print("=" * 72)
	print(title)
	print("=" * 72)


def _scan_imports(script: str) -> list[str]:
	"""Return the offending import lines (if any) from a Server Script body."""
	import ast

	try:
		tree = ast.parse(script or "")
	except SyntaxError as e:
		return [f"<syntax error: {e.msg}>"]
	offenders = []
	for node in ast.walk(tree):
		if isinstance(node, (ast.Import, ast.ImportFrom)):
			if isinstance(node, ast.Import):
				names = ", ".join(a.name for a in node.names)
				offenders.append(f"line {node.lineno}: import {names}")
			else:
				mod = node.module or ""
				names = ", ".join(a.name for a in node.names)
				offenders.append(f"line {node.lineno}: from {mod} import {names}")
	return offenders


def _summarize_changeset(cs_doc) -> dict:
	"""Turn an Alfred Changeset doc into a compact summary."""
	try:
		changes = json.loads(cs_doc.changes) if cs_doc.changes else []
	except json.JSONDecodeError:
		return {"error": "changes field not parseable JSON"}

	try:
		issues = json.loads(cs_doc.dry_run_issues) if cs_doc.dry_run_issues else []
	except json.JSONDecodeError:
		issues = []

	summary = {
		"changeset_name": cs_doc.name,
		"status": cs_doc.status,
		"dry_run_valid": int(cs_doc.dry_run_valid or 0),
		"dry_run_issues_count": len(issues),
		"dry_run_issues_sample": issues[:3],
		"changes_count": len(changes),
		"primitives": [c.get("doctype") for c in changes],
		"items": [],
	}

	for idx, change in enumerate(changes):
		item = {
			"n": idx,
			"op": change.get("op"),
			"doctype": change.get("doctype"),
			"target": (change.get("data") or {}).get("name"),
		}
		data = change.get("data") or {}
		if change.get("doctype") == "Server Script":
			item["reference_doctype"] = data.get("reference_doctype")
			item["doctype_event"] = data.get("doctype_event")
			item["script_type"] = data.get("script_type")
			body = data.get("script") or ""
			item["script_length"] = len(body)
			item["import_violations"] = _scan_imports(body)
			item["script_preview"] = body[:400]
			# Signal: does the body reference the user-facing error message?
			item["mentions_24_years"] = "24" in body
			item["uses_date_diff"] = "date_diff" in body
		if change.get("doctype") == "Custom Field":
			item["dt"] = data.get("dt")
			item["fieldname"] = data.get("fieldname")
			item["fieldtype"] = data.get("fieldtype")
		summary["items"].append(item)

	# Sales Order bleed-through check (the original regression signature).
	sales_order_mentions = []
	for item in summary["items"]:
		if "Sales Order" in json.dumps(item):
			sales_order_mentions.append(item.get("n"))
	summary["sales_order_bleedthrough"] = sales_order_mentions

	return summary


def run():
	_banner("FKB LIVE SMOKE")
	print(f"Prompt: {PROMPT}")

	# 1. Processing-app health
	_banner("1. Processing app health")
	from alfred_client.alfred_settings.page.alfred_chat.alfred_chat import get_conversation_health
	# health check requires an existing conversation; create one first below.

	# 2. Create a fresh conversation
	_banner("2. Create a conversation")
	from alfred_client.alfred_settings.page.alfred_chat.alfred_chat import (
		create_conversation, send_message, get_messages,
	)
	conv = create_conversation()
	conv_name = conv["name"]
	print(f"Created conversation: {conv_name}")

	# Initial health probe
	try:
		health = get_conversation_health(conv_name)
		print(f"Background job ready: {health.get('background_job_running')}")
		print(f"Processing app reachable: {health.get('processing_app_reachable')}")
		print(f"Redis queue depth: {health.get('redis_queue_depth')}")
	except Exception as e:
		print(f"  Health check error: {e}")

	# 3. Send the prompt (mode=auto lets orchestrator classify)
	_banner("3. Send prompt")
	result = send_message(conv_name, PROMPT, mode="auto")
	print(f"send_message result: {json.dumps(result, default=str)}")
	prompt_sent_at = frappe.utils.now_datetime()

	# 4. Poll for a changeset
	_banner("4. Poll for result (may take 1-5 min for cold LLM)")
	deadline = time.time() + POLL_TIMEOUT_SEC
	changeset_name = None
	last_status = None
	tick = 0
	while time.time() < deadline:
		conv_doc = frappe.get_doc("Alfred Conversation", conv_name)
		status = conv_doc.status
		current_agent = conv_doc.current_agent
		if status != last_status or (tick % 6 == 0):
			print(f"  [{int(time.time()-prompt_sent_at.timestamp()):>4}s] "
			      f"status={status}, current_agent={current_agent!r}")
			last_status = status
		# Look for a changeset that landed AFTER we sent the prompt
		cs_rows = frappe.get_all(
			"Alfred Changeset",
			filters={
				"conversation": conv_name,
				"creation": [">", prompt_sent_at],
			},
			fields=["name", "status", "creation"],
			order_by="creation desc",
			limit_page_length=5,
		)
		if cs_rows:
			changeset_name = cs_rows[0]["name"]
			print(f"  >> changeset appeared: {changeset_name} ({cs_rows[0]['status']})")
			break
		# Also break if status moved to terminal without a changeset
		if status in ("Completed", "Failed", "Escalated"):
			print(f"  >> conversation terminal without changeset: {status}")
			break
		time.sleep(POLL_INTERVAL_SEC)
		tick += 1

	# 5. Fetch messages - might contain error / chat_reply / orchestrator mode_switch
	_banner("5. Messages produced")
	messages = get_messages(conv_name)
	for m in messages:
		kind = m.get("message_type") or "text"
		content = (m.get("content") or "")[:200]
		agent = m.get("agent_name") or m.get("role")
		print(f"  [{kind}] from {agent}: {content}")

	# 6. Inspect the changeset if one landed
	if changeset_name:
		_banner("6. Changeset analysis")
		cs_doc = frappe.get_doc("Alfred Changeset", changeset_name)
		summary = _summarize_changeset(cs_doc)
		print(json.dumps(summary, indent=2, default=str))

		# Verdict
		_banner("VERDICT")
		verdicts = []
		if summary.get("changes_count", 0) == 0:
			verdicts.append("FAIL: empty changeset")
		if summary.get("sales_order_bleedthrough"):
			verdicts.append(
				f"FAIL: Sales Order bleedthrough in items {summary['sales_order_bleedthrough']}"
			)
		primitives = set(summary.get("primitives", []))
		if "Notification" in primitives and "Server Script" not in primitives:
			verdicts.append("FAIL: routed to Notification instead of Server Script")
		if "DocType" in primitives:
			verdicts.append("WARN: DocType in changeset - check if new entity was actually needed")
		for item in summary.get("items", []):
			if item.get("doctype") == "Server Script" and item.get("import_violations"):
				verdicts.append(
					f"FAIL: item {item['n']} contains imports: {item['import_violations']}"
				)
		if summary.get("dry_run_valid") == 0:
			verdicts.append(
				f"FAIL: dry-run invalid with {summary.get('dry_run_issues_count')} issues"
			)

		# Positive signals
		positives = []
		for item in summary.get("items", []):
			if item.get("doctype") == "Server Script":
				if item.get("reference_doctype") == "Employee":
					positives.append("Server Script targets Employee")
				if item.get("mentions_24_years"):
					positives.append("script body mentions 24 years")
				if item.get("uses_date_diff"):
					positives.append("script uses frappe.utils.date_diff (pre-bound helper)")
				if not item.get("import_violations"):
					positives.append("script has no `import` statements")

		if verdicts:
			print("ISSUES:")
			for v in verdicts:
				print(f"  - {v}")
		else:
			print("ALL CHECKS PASSED")
		if positives:
			print()
			print("POSITIVE SIGNALS:")
			for p in positives:
				print(f"  - {p}")
	else:
		_banner("6. Changeset analysis")
		print("No changeset landed within the polling window.")
		conv_doc = frappe.get_doc("Alfred Conversation", conv_name)
		print(f"Final conversation status: {conv_doc.status}")
		print(f"Final current_agent: {conv_doc.current_agent!r}")

	# 7. Cleanup
	_banner("7. Cleanup")
	if os.environ.get("KEEP_SMOKE_CONV") == "1":
		print(f"KEEP_SMOKE_CONV=1 - leaving {conv_name} for inspection")
	else:
		print(f"Deleting conversation {conv_name}")
		try:
			from alfred_client.alfred_settings.page.alfred_chat.alfred_chat import delete_conversation
			delete_conversation(conv_name)
		except Exception as e:
			print(f"  cleanup failed (fine to ignore): {e}")
