"""Frappe-whitelisted RPC endpoints for the websocket_client package.

These four functions form the public RPC surface the Vue frontend (and
external integrations) call into. Their dotted paths MUST stay stable -
``alfred_client.api.websocket_client.<fn>`` - because Frappe's RPC layer
imports the module at the requested path and looks up the named
attribute. The package's ``__init__.py`` re-exports each one so callers
who type the package-root path see the function regardless of which
submodule it physically lives in.

Endpoints:
  - ``send_message``       - test/CLI helper; writes a user message + Redis push
  - ``start_conversation`` - idempotent enqueue of ``_connection_manager``
  - ``stop_conversation``  - hard close of the connection manager via Redis pub/sub
  - ``cancel_run``         - graceful cancel via the durable queue
"""

from __future__ import annotations

import json
import logging
import uuid

import frappe

from alfred_client.api.websocket_client._constants import (
	_DISCONNECTED_QUEUE_MAX_LEN,
	_REDIS_CHANNEL_PREFIX,
)
from alfred_client.api.websocket_client._introspection import (
	_conversation_job_in_flight,
	_long_queue_worker_count,
)

logger = logging.getLogger("alfred.ws_client")


@frappe.whitelist()
def send_message(conversation_name, message, msg_type="prompt"):
	"""Send a message to the Processing App via durable Redis queue.

	NOTE: This is a **test/CLI utility**, NOT the function the chat UI calls.
	The UI calls `alfred_client.alfred_settings.page.alfred_chat.alfred_chat.send_message`
	which additionally creates an Alfred Message row and starts the connection
	manager. Keep this lightweight helper for tests and scripts.

	Caller must have write permission on the target conversation - without
	that gate, the ignore_permissions=True insert below would let any user
	with Alfred role pollute any other user's chat.

	Pushes the message to a Redis list (durable) and sends a pub/sub
	notification. The connection manager drains the list and forwards
	over the WebSocket. Safe to call from any Frappe worker.
	"""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()
	frappe.has_permission(
		"Alfred Conversation", ptype="write", doc=conversation_name, throw=True,
	)

	# Store the user message in the database
	frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "user",
		"message_type": "text",
		"content": message,
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	# Publish to Redis - connection manager will forward to Processing App
	msg = json.dumps({
		"msg_id": str(uuid.uuid4()),
		"type": msg_type,
		"data": {"text": message, "user": frappe.session.user},
	})
	redis_conn = frappe.cache()
	channel = f"{_REDIS_CHANNEL_PREFIX}{conversation_name}"
	# Push to durable queue first (connection manager drains on connect/reconnect),
	# then notify via pub/sub for immediate delivery if already listening.
	queue_key = f"{_REDIS_CHANNEL_PREFIX}queue:{conversation_name}"
	redis_conn.rpush(queue_key, msg)
	# Cap the queue (see _DISCONNECTED_QUEUE_MAX_LEN comment for rationale).
	redis_conn.ltrim(queue_key, -_DISCONNECTED_QUEUE_MAX_LEN, -1)
	# Pub/sub is just a notification - the actual message is read from the queue.
	redis_conn.publish(channel, "__notify__")


@frappe.whitelist()
def start_conversation(conversation_name):
	"""Start (or re-use) the WebSocket connection manager for a conversation.

	Idempotent: if a `_connection_manager` job is already queued or running
	for `conversation_name`, this is a no-op so repeated calls do not stack
	redundant RQ jobs (openConversation, send_message, and the UI watchdog
	all call this safely).

	Each active conversation consumes one slot on the 'long' RQ worker queue
	for up to 7200s. If no worker is serving the long queue at all, we
	surface that as an error rather than silently enqueueing a job that will
	never run.

	SECURITY:
	  - Caller must have read permission on the conversation (owner, shared,
	    or System Manager). Without this, a user with the Alfred role could
	    boot a worker for any conversation ID they could guess.
	  - The connection manager runs as the CONVERSATION OWNER, not the
	    caller. This matters because the MCP dispatch path sets
	    frappe.session.user from this value; downstream tool calls see the
	    owner's permissions, which is the correct trust boundary for an
	    agent working on the owner's problem. Before this fix, starting a
	    shared conversation would silently run all MCP calls as the caller,
	    letting the agent see data the owner can't and leaking it into the
	    conversation.

	Returns a small status dict so the UI can show a targeted toast:
	  - {"status": "already_running"} - a manager is already in flight.
	  - {"status": "enqueued"} - a new job was enqueued.
	  - {"status": "no_worker"} - long queue has zero workers, job not
	    enqueued; caller should prompt the operator to start worker_long.
	"""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()

	if not frappe.db.exists("Alfred Conversation", conversation_name):
		frappe.throw(frappe._("Conversation not found"), frappe.DoesNotExistError)

	frappe.has_permission(
		"Alfred Conversation", ptype="read", doc=conversation_name, throw=True,
	)

	# Idempotent: if a manager is already in flight, do nothing. This also
	# stops repeated UI calls from piling up hundreds of jobs when
	# worker_long is dead and jobs cannot drain.
	if _conversation_job_in_flight(conversation_name):
		return {"status": "already_running"}

	worker_count = _long_queue_worker_count()
	if worker_count == 0:
		# No worker is serving the long queue. Enqueueing a job would just
		# make it sit forever. Tell the caller so the UI can surface a
		# clear "worker_long not running" toast instead of an invisible
		# failure.
		frappe.logger().error(
			"Alfred: refusing to enqueue _connection_manager for %s - no worker is serving the 'long' queue",
			conversation_name,
		)
		return {
			"status": "no_worker",
			"message": frappe._(
				"No background worker is serving the 'long' RQ queue. "
				"Ask your admin to start worker_long (check Procfile + bench restart)."
			),
		}

	# The connection manager MUST run as the conversation's owner. MCP tool
	# calls made from the processing app run under frappe.session.user inside
	# the manager; using the caller's user here would let a shared-conv user
	# see data the owner can't (or vice versa) and would pollute the agent's
	# world view.
	conversation_owner = frappe.db.get_value(
		"Alfred Conversation", conversation_name, "user",
	)
	if not conversation_owner:
		frappe.throw(
			frappe._("Conversation {0} has no owner recorded").format(conversation_name)
		)

	# Soft check: count distinct Alfred conversations with a queued/started
	# connection manager. Pulling by key="conversation_name" naturally skips
	# unrelated long-queue jobs (Frappe search indexing, etc.) since only
	# Alfred's _connection_manager enqueues with that kwarg. Deduping on the
	# set also stops legacy duplicate jobs (from before the idempotency
	# guard landed) from inflating the count.
	try:
		from frappe.utils.background_jobs import get_jobs
		site_jobs = get_jobs(
			site=frappe.local.site, queue="long", key="conversation_name"
		) or {}
		distinct_convs: set[str] = set()
		for conv_names in site_jobs.values():
			for cname in conv_names:
				if cname:
					distinct_convs.add(cname)
		if len(distinct_convs) > 10:
			frappe.msgprint(
				frappe._(
					"Warning: {0} active Alfred conversations are already running. "
					"This conversation may take longer to start - close unused "
					"conversations if it doesn't start within 30 seconds."
				).format(len(distinct_convs)),
				indicator="orange",
				alert=True,
			)
	except Exception:
		pass  # Best-effort check; don't block on saturation detection

	# CRITICAL: the dotted path here MUST resolve at job-execution time.
	# In-flight jobs in Redis store this string verbatim; the package's
	# __init__.py re-exports _connection_manager so the OLD path
	# (alfred_client.api.websocket_client._connection_manager) keeps
	# resolving even though the function physically lives in
	# alfred_client.api.websocket_client._manager. Do not change this
	# string without first draining the long-queue.
	frappe.enqueue(
		"alfred_client.api.websocket_client._connection_manager",
		conversation_name=conversation_name,
		user=conversation_owner,
		queue="long",
		timeout=7200,
	)
	return {"status": "enqueued"}


@frappe.whitelist()
def stop_conversation(conversation_name):
	"""Stop the WebSocket connection for a conversation.

	Write permission required - stopping a live run in the middle of a
	pipeline is effectively cancelling another user's work.
	"""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()
	frappe.has_permission(
		"Alfred Conversation", ptype="write", doc=conversation_name, throw=True,
	)

	redis_conn = frappe.cache()
	channel = f"{_REDIS_CHANNEL_PREFIX}{conversation_name}"
	redis_conn.publish(channel, "__shutdown__")


@frappe.whitelist()
def cancel_run(conversation_name):
	"""Graceful cancel of an in-flight agent pipeline.

	Unlike stop_conversation (which hard-closes the outbound WS), this pushes
	a `{"type": "cancel"}` message through the existing durable queue so the
	processing app can flip should_stop at the next phase boundary and exit
	via the normal error path. The WS stays open, so the user can keep
	chatting in the same conversation after the run is cancelled.

	Also flips the Alfred Conversation status to "Cancelled" locally so the
	UI stays honest even if the processing app is down or the run already
	completed by the time the cancel arrives.
	"""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()
	frappe.has_permission(
		"Alfred Conversation", ptype="write", doc=conversation_name, throw=True,
	)

	msg = json.dumps({
		"msg_id": str(uuid.uuid4()),
		"type": "cancel",
		"data": {"user": frappe.session.user},
	})
	redis_conn = frappe.cache()
	channel = f"{_REDIS_CHANNEL_PREFIX}{conversation_name}"
	queue_key = f"{_REDIS_CHANNEL_PREFIX}queue:{conversation_name}"
	redis_conn.rpush(queue_key, msg)
	# Cap the queue (see _DISCONNECTED_QUEUE_MAX_LEN comment for rationale).
	redis_conn.ltrim(queue_key, -_DISCONNECTED_QUEUE_MAX_LEN, -1)
	redis_conn.publish(channel, "__notify__")

	frappe.db.set_value(
		"Alfred Conversation", conversation_name, "status", "Cancelled",
		update_modified=True,
	)
	frappe.db.commit()
