"""Bench CLI commands for Alfred Client.

Surfaces long-queue diagnostics and recovery that previously required
hand-written `redis-cli` one-liners. Invoked as:

    bench --site <site> alfred-reap [--all] [--conv <id>] [--idle] [--yes]

Frappe auto-discovers Click commands via the ``commands`` list exported
from this module, so no wiring in ``hooks.py`` is needed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import click
import frappe
from frappe.commands import pass_context

_SHUTDOWN_CHANNEL_PREFIX = "alfred:ws:outbound:"


def _iso_now() -> datetime:
	return datetime.now(timezone.utc)


def _parse_heartbeat(raw) -> datetime | None:
	if not raw:
		return None
	if isinstance(raw, bytes):
		raw = raw.decode("utf-8", errors="ignore")
	try:
		return datetime.fromisoformat(raw.replace("Z", "+00:00"))
	except ValueError:
		return None


def _collect_long_queue_managers() -> list[dict]:
	"""Walk RQ and return one row per `_connection_manager` that is running
	or queued on the long queue. Each row has enough detail for a human to
	decide which ones to reap.
	"""
	from frappe.utils.background_jobs import get_queue, get_redis_conn
	from rq import Worker

	conn = get_redis_conn()
	queue = get_queue("long")
	now = _iso_now()
	rows: list[dict] = []

	# Running managers: iterate workers and inspect their current_job.
	for w in Worker.all(queue=queue):
		job_id_raw = conn.hget(w.key, "current_job")
		if not job_id_raw:
			continue
		job_id = (
			job_id_raw.decode("utf-8", errors="ignore")
			if isinstance(job_id_raw, bytes)
			else job_id_raw
		)
		job_key = f"rq:job:{job_id}"
		desc_raw = conn.hget(job_key, "description")
		desc = (
			desc_raw.decode("utf-8", errors="ignore")
			if isinstance(desc_raw, bytes)
			else (desc_raw or "")
		)
		if "_connection_manager" not in desc:
			continue
		conv = _extract_conversation_name(desc)
		started_at_raw = conn.hget(job_key, "started_at")
		started_at = _parse_heartbeat(started_at_raw) or now
		rows.append(
			{
				"conversation": conv,
				"state": "running",
				"job_id": job_id,
				"worker": w.name,
				"age_seconds": int((now - started_at).total_seconds()),
			}
		)

	# Queued managers: scan the long queue list for our jobs.
	queue_key = f"rq:queue:{queue.name}"
	queued_ids = conn.lrange(queue_key, 0, -1) or []
	for raw in queued_ids:
		job_id = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
		job_key = f"rq:job:{job_id}"
		desc_raw = conn.hget(job_key, "description")
		desc = (
			desc_raw.decode("utf-8", errors="ignore")
			if isinstance(desc_raw, bytes)
			else (desc_raw or "")
		)
		if "_connection_manager" not in desc:
			continue
		conv = _extract_conversation_name(desc)
		enqueued_at = _parse_heartbeat(conn.hget(job_key, "enqueued_at")) or now
		rows.append(
			{
				"conversation": conv,
				"state": "queued",
				"job_id": job_id,
				"worker": None,
				"age_seconds": int((now - enqueued_at).total_seconds()),
			}
		)

	return rows


def _extract_conversation_name(description: str) -> str:
	"""Pull conversation_name out of the job description kwargs blob."""
	marker = "'conversation_name': '"
	start = description.find(marker)
	if start < 0:
		return "?"
	start += len(marker)
	end = description.find("'", start)
	if end < 0:
		return "?"
	return description[start:end]


def _shutdown_conversation(conversation_name: str) -> int:
	"""Publish __shutdown__ on the conversation's Redis channel. Returns the
	number of subscribers that received the signal (0 = no live manager)."""
	channel = f"{_SHUTDOWN_CHANNEL_PREFIX}{conversation_name}"
	return int(frappe.cache().publish(channel, "__shutdown__") or 0)


def _format_age(seconds: int) -> str:
	if seconds < 60:
		return f"{seconds}s"
	if seconds < 3600:
		return f"{seconds // 60}m {seconds % 60}s"
	return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


@click.command("alfred-reap")
@click.option(
	"--all",
	"reap_all",
	is_flag=True,
	help="Reap every running or queued connection manager without prompting.",
)
@click.option(
	"--conv",
	"target_conv",
	default=None,
	help="Reap only this conversation's manager (by name).",
)
@click.option(
	"--idle",
	"idle_only",
	is_flag=True,
	help="Reap only managers whose conversation has not received a user message"
	" in the last hour. Useful after a bench restart when auto-requeued"
	" managers are holding worker slots with nothing to do.",
)
@click.option(
	"--yes",
	"assume_yes",
	is_flag=True,
	help="Skip the interactive confirmation (pair with --all or --conv).",
)
@pass_context
def alfred_reap(context, reap_all, target_conv, idle_only, assume_yes):
	"""List active Alfred connection managers and optionally shut them down.

	Every live chat conversation occupies one slot on the RQ 'long' worker
	queue. When all slots are held by idle managers (common after a bench
	restart auto-requeues them), new conversations sit in queue until one
	releases. Use this command to see who is holding slots and, when
	appropriate, free them up.
	"""
	site = _require_site(context)
	frappe.init(site=site)
	frappe.connect(site=site)
	try:
		rows = _collect_long_queue_managers()
		if not rows:
			click.echo("No connection managers running or queued on the long queue.")
			return

		_render_table(rows)

		if target_conv:
			targets = [r for r in rows if r["conversation"] == target_conv]
			if not targets:
				click.echo(
					f"\nNo running or queued manager for conversation '{target_conv}'.",
					err=True,
				)
				sys.exit(1)
		elif reap_all:
			targets = list(rows)
		elif idle_only:
			targets = _filter_idle(rows)
			if not targets:
				click.echo("\nNo idle managers to reap.")
				return
		else:
			click.echo(
				"\nPass --conv <id>, --all, or --idle to reap. "
				"This was a dry-run listing.",
			)
			return

		click.echo("")
		if not assume_yes:
			conv_list = ", ".join(t["conversation"] for t in targets)
			if not click.confirm(
				f"Shut down {len(targets)} manager(s) "
				f"({conv_list})? This is safe to run - "
				"closed conversations will auto-reconnect when reopened.",
				default=False,
			):
				click.echo("Aborted.")
				return

		for t in targets:
			subs = _shutdown_conversation(t["conversation"])
			click.echo(
				f"  {t['conversation']} ({t['state']}) "
				f"-> {'signal delivered' if subs else 'no live subscriber'}"
			)
	finally:
		frappe.destroy()


def _filter_idle(rows: list[dict]) -> list[dict]:
	"""Return managers whose Alfred Conversation has not seen a user prompt
	in the last hour. Uses the conversation's modified timestamp as a proxy.
	"""
	idle: list[dict] = []
	for r in rows:
		conv = r["conversation"]
		if conv == "?":
			continue
		try:
			modified = frappe.db.get_value("Alfred Conversation", conv, "modified")
		except Exception:
			modified = None
		if modified is None:
			continue
		# modified is a naive datetime in local tz; convert to UTC.
		try:
			from frappe.utils import get_datetime, now_datetime
			mod_dt = get_datetime(modified)
			age = (now_datetime() - mod_dt).total_seconds()
		except Exception:
			continue
		if age > 3600:
			idle.append(r)
	return idle


def _render_table(rows: list[dict]) -> None:
	click.echo(
		f"{'STATE':<8} {'CONVERSATION':<24} {'AGE':<10} {'WORKER/JOB':<40}"
	)
	click.echo("-" * 84)
	for r in rows:
		worker_or_job = r["worker"] or r["job_id"][:36]
		click.echo(
			f"{r['state']:<8} {r['conversation']:<24} "
			f"{_format_age(r['age_seconds']):<10} {worker_or_job:<40}"
		)


def _require_site(context) -> str:
	# @pass_context from frappe.commands hands us the CliCtxObj directly (not
	# the Click Context). Sites live on ctx.sites as a list.
	sites = getattr(context, "sites", None) or []
	if isinstance(sites, str):
		sites = [sites]
	site = sites[0] if sites else None
	if not site:
		click.echo(
			"alfred-reap needs a site. Run with: bench --site <site> alfred-reap",
			err=True,
		)
		sys.exit(1)
	return site


commands = [alfred_reap]
