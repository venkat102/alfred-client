"""RQ worker introspection helpers used by ``start_conversation``.

Two checks gate enqueueing a new ``_connection_manager`` job:
  1. Is there a live worker on the long queue at all? If zero, enqueueing
     would just stack jobs that never run - return 'no_worker' to the
     caller so the UI can surface "ask your admin to start worker_long".
  2. Is a manager already in flight for this conversation? If yes, the
     enqueue is a no-op (idempotent) - the existing job already owns the
     WS for that conversation.

Both are best-effort lookups against RQ's Redis state. They never raise -
on introspection failure they default to "permit the enqueue", which is
the safer side of the trade (a redundant job is benign; a missed
enqueue means the user's chat hangs forever).
"""

from __future__ import annotations

import logging

import frappe

from alfred_client.api.websocket_client._constants import _WORKER_HEARTBEAT_STALE_SECONDS

logger = logging.getLogger("alfred.ws_client")


def _long_queue_worker_count() -> int:
	"""Return the number of LIVE RQ workers serving the 'long' queue for this
	site. A "live" worker is one whose last_heartbeat field is present and
	within _WORKER_HEARTBEAT_STALE_SECONDS of now.

	Worker.all(queue=q) reads rq:workers:<qname>, which can accumulate
	zombie entries when a worker dies without running its cleanup - common
	on dev laptops after sleep/wake, Ctrl-C at the wrong moment, or a hard
	kill. Those zombies are not pruned automatically because their hash has
	no last_heartbeat for RQ to compare. Filtering by heartbeat freshness
	here means a zombie will not inflate the count and trick
	start_conversation into enqueueing jobs that cannot actually run.
	"""
	try:
		from datetime import datetime, timedelta, timezone

		from rq import Worker

		from frappe.utils.background_jobs import get_queue, get_redis_conn

		conn = get_redis_conn()
		threshold = datetime.now(timezone.utc) - timedelta(
			seconds=_WORKER_HEARTBEAT_STALE_SECONDS
		)
		live = 0
		for w in Worker.all(queue=get_queue("long")):
			hb_raw = conn.hget(w.key, "last_heartbeat")
			if not hb_raw:
				continue  # zombie: no heartbeat recorded at all
			if isinstance(hb_raw, bytes):
				hb_raw = hb_raw.decode("utf-8", errors="ignore")
			# RQ writes ISO 8601 with trailing Z; fromisoformat handles +00:00
			# but not Z directly until 3.11+. Normalise for safety.
			hb_iso = hb_raw.replace("Z", "+00:00")
			try:
				hb = datetime.fromisoformat(hb_iso)
			except ValueError:
				continue  # unparseable -> treat as zombie
			if hb < threshold:
				continue  # heartbeat is too old; worker is dead
			live += 1
		return live
	except Exception:
		return -1  # unknown


def _conversation_job_in_flight(conversation_name: str) -> bool:
	"""Check whether a `_connection_manager` job for this conversation is
	already queued or started on the long queue. Used to keep
	start_conversation idempotent so repeated calls (on every openConversation,
	on every send_message) do not stack redundant RQ jobs.

	Uses get_jobs(key="conversation_name") which walks each job's nested
	kwargs for the "conversation_name" key - the one Alfred passes when
	enqueueing _connection_manager. Jobs without that kwarg (Frappe search
	indexing, etc.) are naturally excluded.
	"""
	try:
		from frappe.utils.background_jobs import get_jobs

		site_jobs = get_jobs(
			site=frappe.local.site, queue="long", key="conversation_name"
		) or {}
		for conv_names in site_jobs.values():
			if conversation_name in conv_names:
				return True
		return False
	except Exception:
		# On introspection failure, err on the side of enqueueing so the
		# user is not blocked. Duplicate jobs are less bad than no jobs.
		return False
