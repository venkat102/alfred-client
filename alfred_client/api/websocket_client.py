"""Persistent WebSocket client connecting the Client App to the Processing App.

Architecture:
  Browser <-> Frappe (Socket.IO) <-> This module <-> Processing App (WebSocket)

Message flow:
  - Outbound: send_message() pushes to Redis list + notifies via pub/sub ->
              connection manager drains list -> sends via WS
  - Inbound: WS message arrives -> connection manager routes -> frappe.publish_realtime() to browser

The connection manager runs as a single long-running background job per conversation
(enqueued to the "long" RQ queue - requires worker_long in Procfile).
Messages are durably queued in a Redis list; pub/sub is used only as a wakeup
notification to avoid polling. This ensures no messages are lost if the connection
manager isn't subscribed at the moment send_message() fires.
"""

import asyncio
import json
import logging
import time
import uuid

import frappe
import jwt as pyjwt

logger = logging.getLogger("alfred.ws_client")

# Redis channel prefix for message passing between Frappe workers and the connection manager
_REDIS_CHANNEL_PREFIX = "alfred:ws:outbound:"


def _get_site_id():
	"""Get the canonical site_id used for multi-tenant isolation in the Processing App.

	Returns the bare Frappe site name (e.g. "dev.alfred"), NOT the full URL.
	The Processing App validates this against ^[a-zA-Z0-9._-]+$ because it's used
	as a Redis key namespace - characters like ":" or "/" would either fail
	validation or break key parsing.

	If the Processing App ever needs the full URL (e.g. for Admin Portal callbacks),
	pass it separately via site_config["site_url"], not through site_id.
	"""
	return frappe.local.site


def _generate_jwt(api_key, user=None, roles=None):
	"""Generate a signed JWT for WebSocket handshake."""
	if user is None:
		user = frappe.session.user
	if roles is None:
		roles = frappe.get_roles(user)

	now = int(time.time())
	payload = {
		"user": user,
		"roles": roles,
		"site_id": _get_site_id(),
		"iat": now,
		"exp": now + 86400,
	}
	return pyjwt.encode(payload, api_key, algorithm="HS256")


def _get_site_config():
	"""Read LLM and limit configuration from Alfred Settings."""
	settings = frappe.get_single("Alfred Settings")
	return {
		"site_id": _get_site_id(),
		"llm_provider": settings.llm_provider,
		"llm_model": settings.llm_model,
		"llm_api_key": settings.get_password("llm_api_key") if settings.llm_api_key else "",
		"llm_base_url": settings.llm_base_url or "",
		"llm_max_tokens": settings.llm_max_tokens,
		"llm_temperature": settings.llm_temperature,
		"llm_num_ctx": settings.llm_num_ctx,
		# Per-tier model overrides (empty = use default model)
		"llm_model_triage": settings.llm_model_triage or "",
		"llm_model_triage_num_ctx": settings.llm_model_triage_num_ctx or 0,
		"llm_model_reasoning": settings.llm_model_reasoning or "",
		"llm_model_reasoning_num_ctx": settings.llm_model_reasoning_num_ctx or 0,
		"llm_model_agent": settings.llm_model_agent or "",
		"llm_model_agent_num_ctx": settings.llm_model_agent_num_ctx or 0,
		"pipeline_mode": getattr(settings, "pipeline_mode", None) or "full",
		"max_retries_per_agent": settings.max_retries_per_agent,
		"max_tasks_per_user_per_hour": settings.max_tasks_per_user_per_hour,
		"task_timeout_seconds": settings.task_timeout_seconds,
		"enable_auto_deploy": settings.enable_auto_deploy,
	}


def _route_incoming_message(message, user, conversation_name):
	"""Route an incoming message from the Processing App to the browser.

	Uses frappe.publish_realtime() to forward events to the correct user.
	"""
	msg_type = message.get("type", "")
	data = message.get("data", {})

	if msg_type == "ping":
		return

	logger.info("Routing message: type=%s, user=%s, conversation=%s", msg_type, user, conversation_name)

	event_map = {
		"agent_status": "alfred_agent_status",
		"agent_activity": "alfred_activity",
		"question": "alfred_question",
		"preview": "alfred_preview",
		"changeset": "alfred_preview",
		"error": "alfred_error",
		"echo": "alfred_agent_status",
		"mcp_request": "alfred_mcp_request",
		"deploy": "alfred_deploy",
		"auth_success": None,
		# Three-mode chat (Phase A/B/C): conversational, read-only, and
		# planning handlers stream their output as these new event types.
		# The browser renders them as regular Alfred Message rows, NOT
		# changesets.
		"chat_reply": "alfred_chat_reply",
		"insights_reply": "alfred_insights_reply",
		"plan_doc": "alfred_plan_doc",
		"mode_switch": "alfred_mode_switch",
		# Graceful user-initiated cancel: the processing app emits this when
		# ctx.stop(code="user_cancel") fires, instead of the generic error
		# event. Separate event so the UI can render it as a neutral system
		# message, not an error banner.
		"run_cancelled": "alfred_run_cancelled",
	}

	event_name = event_map.get(msg_type)
	if event_name:
		logger.info("Publishing realtime event: %s -> %s", msg_type, event_name)
		frappe.publish_realtime(event_name, data, user=user, after_commit=False)

	# Persist chat / insights replies and plan docs as Alfred Message
	# rows so the conversation scrollback survives page reload. None of
	# them go through the changeset approval flow, so this is the only
	# place they get durably stored.
	if msg_type in ("chat_reply", "insights_reply"):
		try:
			_store_agent_reply_message(conversation_name, msg_type, data)
		except Exception as e:
			logger.warning("Failed to store %s message: %s", msg_type, e)
	elif msg_type == "plan_doc":
		try:
			_store_plan_doc_message(conversation_name, data)
		except Exception as e:
			logger.warning("Failed to store plan_doc message: %s", e)

	# Store changeset previews in the database and notify browser with the doc name
	if msg_type in ("preview", "changeset") and data.get("changes"):
		try:
			# Dry-run result is optional - older processing app versions may not send it.
			# Default to {valid: True, issues: []} so the UI treats legacy changesets as
			# valid (matching the prior behavior where no pre-validation existed).
			dry_run = data.get("dry_run") or {"valid": True, "issues": []}
			dry_run_valid = 1 if dry_run.get("valid") else 0
			dry_run_issues = json.dumps(dry_run.get("issues", []))

			changeset = frappe.get_doc({
				"doctype": "Alfred Changeset",
				"conversation": conversation_name,
				"status": "Pending",
				"changes": json.dumps(data.get("changes", [])),
				"dry_run_valid": dry_run_valid,
				"dry_run_issues": dry_run_issues,
				# Explicit owner - ignore_permissions=True bypasses the default
				# session-user assignment, so set it manually so row-level
				# permissions (the conversation owner should see their own
				# changesets) work correctly.
				"owner": user,
			})
			changeset.insert(ignore_permissions=True)
			frappe.db.commit()
			# Re-publish with the saved changeset name so the UI can fetch it
			frappe.publish_realtime(
				"alfred_preview",
				{"changeset_name": changeset.name, "conversation": conversation_name},
				user=user,
				after_commit=False,
			)
		except Exception as e:
			logger.error("Failed to store changeset: %s", e)


def _store_agent_reply_message(conversation_name, msg_type, data):
	"""Store a chat_reply or insights_reply as an Alfred Message row.

	Three-mode chat (Phase A/B): these messages are conversational / read-only
	and never become changesets, but the user should see them on page reload
	the same way they see dev-mode agent replies.
	"""
	reply_text = (data or {}).get("reply") or ""
	if not reply_text:
		return

	# The current Alfred Message.message_type select enum is
	# text / question / preview / changeset / status / error. Store
	# chat_reply / insights_reply as `text` with a mode marker in
	# metadata so the frontend can distinguish them (and so we don't
	# have to migrate the select field for Phase A/B).
	metadata = {
		"mode": "chat" if msg_type == "chat_reply" else "insights",
		"handler": msg_type,
	}

	msg = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "agent",
		"agent_name": "Alfred" if msg_type == "chat_reply" else "Insights",
		"message_type": "text",
		"content": reply_text,
		"metadata": json.dumps(metadata),
	})
	msg.insert(ignore_permissions=True)
	frappe.db.commit()


def _store_plan_doc_message(conversation_name, data):
	"""Store a plan_doc (Phase C) as an Alfred Message row.

	The plan itself is embedded in metadata.plan so the frontend's
	PlanDocPanel can read it back after a page reload. Content is set to
	the plan title so list views and search still show something useful
	even if the JSON isn't unpacked.
	"""
	plan = (data or {}).get("plan") or {}
	title = plan.get("title") or "Plan"

	metadata = {
		"mode": "plan",
		"handler": "plan_doc",
		"plan": plan,
	}

	msg = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "agent",
		"agent_name": "Planner",
		"message_type": "text",
		"content": title,
		"metadata": json.dumps(metadata),
	})
	msg.insert(ignore_permissions=True)
	frappe.db.commit()


def _publish_connection_event(user, conversation_name, state, message="", detail=""):
	"""Publish a connection lifecycle event to the browser.

	Swallows MySQL staleness errors so a dead DB connection cannot crash the
	connection manager's outer retry loop. If the publish fails for any other
	reason we also swallow - this is a notification channel, not critical path.
	"""
	try:
		_reconnect_db_if_stale()
		frappe.publish_realtime(
			"alfred_connection_status",
			{
				"conversation": conversation_name,
				"state": state,  # "starting", "connected", "disconnected", "reconnecting", "failed", "stopped"
				"message": message,
				"detail": detail,
				"timestamp": time.time(),
			},
			user=user,
			after_commit=False,
		)
	except Exception as e:
		logger.warning(
			"Failed to publish connection event (state=%s, conv=%s): %s",
			state, conversation_name, e,
		)


def _reconnect_db_if_stale():
	"""Reconnect MySQL if the worker's connection went stale.

	RQ workers hold DB connections for the lifetime of the job (up to 7200s
	for alfred's long-running connection manager). A `bench restart` or a
	network hiccup can kill the MySQL connection mid-job, leading to
	`(2006, 'Server has gone away')` on the next query. This helper pings
	the DB and reconnects if the ping fails.
	"""
	try:
		frappe.db.sql("SELECT 1")
	except Exception:
		try:
			frappe.db.close()
		except Exception:
			pass
		try:
			frappe.connect(site=frappe.local.site)
		except Exception as e:
			logger.warning("Failed to reconnect DB: %s", e)


def _connection_manager(conversation_name, user):
	"""Long-running background job: manages a single WebSocket connection.

	Runs one asyncio event loop that:
	1. Opens a WebSocket to the Processing App with authenticated handshake
	2. Subscribes to a Redis pub/sub channel for outbound messages
	3. Routes inbound WS messages to the browser via frappe.publish_realtime()
	4. Handles reconnection with exponential backoff
	5. Dispatches MCP (JSON-RPC) tool calls from the Processing App to the
	   Frappe-backed MCP server under the conversation owner's session so
	   permission enforcement works correctly.

	All message sending goes through Redis pub/sub - no cross-loop issues.
	"""
	# Critical: RQ worker jobs start as Administrator by default. MCP tools rely on
	# frappe.session.user for permission_query_conditions row-level filters - set
	# it to the conversation owner so tool calls respect that user's permissions.
	# Capture the original user so we can restore it in the finally block - RQ
	# worker processes handle multiple jobs, and leaving frappe.session.user on
	# a stale value would leak into the next job.
	original_user = frappe.session.user
	frappe.set_user(user)

	_publish_connection_event(user, conversation_name, "starting", "Starting connection manager...")
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)
	try:
		loop.run_until_complete(_connection_loop(conversation_name, user))
	except Exception as e:
		logger.error("Connection manager died for %s: %s", conversation_name, e)
		_publish_connection_event(user, conversation_name, "failed", str(e), "Connection to Processing App failed. Check Alfred Settings.")
		frappe.publish_realtime(
			"alfred_error",
			{"conversation": conversation_name, "error": str(e),
			 "message": "Connection to Processing App failed. Check Alfred Settings."},
			user=user,
		)
	finally:
		_publish_connection_event(user, conversation_name, "stopped", "Connection manager stopped.")
		loop.close()
		# Restore the session user for the RQ worker process.
		try:
			frappe.set_user(original_user)
		except Exception as e:
			logger.warning("Failed to restore session user after connection manager: %s", e)


async def _connection_loop(conversation_name, user):
	"""Core async loop: connect, subscribe Redis channel, route messages."""
	import redis.asyncio as aioredis
	import websockets

	settings = frappe.get_single("Alfred Settings")
	base_url = settings.processing_app_url
	api_key = settings.get_password("api_key")

	if not base_url or not api_key:
		raise ValueError("Alfred Settings: processing_app_url and api_key must be configured")

	# Build WebSocket URL
	if base_url.startswith("http://"):
		ws_url = "ws://" + base_url[7:]
	elif base_url.startswith("https://"):
		ws_url = "wss://" + base_url[8:]
	elif not base_url.startswith(("ws://", "wss://")):
		ws_url = "wss://" + base_url
	else:
		ws_url = base_url
	ws_url = f"{ws_url.rstrip('/')}/ws/{conversation_name}"

	# Connect to the SAME Redis instance that frappe.cache() uses (the cache redis,
	# typically port 13000). send_message in alfred_chat.py uses frappe.cache().rpush()
	# and frappe.cache().publish(), so we MUST use the same instance to see those writes.
	# Using frappe.conf.get("redis_queue") instead would point at port 11000 (RQ's redis),
	# which is a different server - no messages would ever be delivered.
	redis_url = frappe.conf.get("redis_cache") or "redis://localhost:13000"
	redis_client = aioredis.from_url(redis_url, decode_responses=True)
	pubsub = redis_client.pubsub()

	# Frappe's RedisWrapper auto-prefixes LIST keys (rpush/lpop/llen) with "<db_name>|"
	# but does NOT prefix pub/sub channel names. We must match that behavior here:
	#   - channel name → bare (no prefix)
	#   - list key → prefixed with "<db_name>|"
	site_prefix = f"{frappe.conf.get('db_name')}|"
	channel = f"{_REDIS_CHANNEL_PREFIX}{conversation_name}"
	await pubsub.subscribe(channel)

	backoff = 1
	max_backoff = 60
	max_retries = 10  # Give up after 10 consecutive failures instead of looping forever
	retry_count = 0
	last_msg_id = None
	should_stop = False
	# Total lifetime cap for the connection manager job. Prevents a stale
	# browser tab (user closed it without cleanup) from occupying a long-queue
	# worker slot forever. RQ's job timeout is 7200s; we self-exit slightly
	# before that so RQ never has to force-kill us.
	max_lifetime_seconds = int(frappe.conf.get("alfred_conn_max_lifetime") or 6300)
	started_at = time.time()

	while not should_stop:
		if time.time() - started_at > max_lifetime_seconds:
			logger.info(
				"Connection manager reached lifetime cap (%ds) for conversation=%s; exiting",
				max_lifetime_seconds, conversation_name,
			)
			_publish_connection_event(
				user, conversation_name, "stopped",
				"Connection manager lifetime reached. Reopen the chat to reconnect.",
			)
			break
		try:
			async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
				# Authenticated handshake
				handshake = {
					"api_key": api_key,
					"jwt_token": _generate_jwt(api_key, user),
					"site_config": _get_site_config(),
				}
				await ws.send(json.dumps(handshake))

				auth_response = json.loads(await ws.recv())
				if auth_response.get("type") != "auth_success":
					raise ConnectionError(f"Handshake failed: {auth_response}")

				backoff = 1  # Reset on successful connect
				retry_count = 0
				logger.info("Connected to Processing App: conversation=%s", conversation_name)
				_publish_connection_event(user, conversation_name, "connected", "Connected to Processing App")

				# Resume if reconnecting
				if last_msg_id:
					await ws.send(json.dumps({
						"msg_id": str(uuid.uuid4()),
						"type": "resume",
						"data": {"last_msg_id": last_msg_id},
					}))

				# Drain any queued messages that arrived before we were listening.
				# Must use the site-prefixed key to match what frappe.cache().rpush() wrote.
				queue_key = f"{site_prefix}{_REDIS_CHANNEL_PREFIX}queue:{conversation_name}"
				while True:
					queued = await redis_client.lpop(queue_key)
					if not queued:
						break
					logger.info("Draining queued message for %s", conversation_name)
					await ws.send(queued)

				# Run two tasks concurrently:
				# 1. Listen for inbound WS messages (from Processing App)
				# 2. Listen for outbound Redis pub/sub messages (from Frappe workers)
				ws_listen = asyncio.create_task(_listen_ws(ws, user, conversation_name))
				redis_listen = asyncio.create_task(_listen_redis(ws, pubsub, channel, redis_client, queue_key))

				done, pending = await asyncio.wait(
					[ws_listen, redis_listen], return_when=asyncio.FIRST_COMPLETED
				)
				for task in pending:
					task.cancel()
					try:
						await task
					except asyncio.CancelledError:
						pass

				# Check if shutdown was requested
				for task in done:
					exc = task.exception() if not task.cancelled() else None
					if isinstance(exc, _ShutdownRequested):
						should_stop = True

		except _ShutdownRequested:
			should_stop = True
		except Exception as e:
			retry_count += 1
			if retry_count >= max_retries:
				logger.error(
					"Connection manager giving up after %d retries (conversation=%s): %s",
					max_retries, conversation_name, e,
				)
				_publish_connection_event(
					user, conversation_name, "failed",
					f"Gave up after {max_retries} retries. Last error: {e}",
				)
				should_stop = True
			else:
				logger.warning(
					"Connection lost (conversation=%s): %s. Retry %d/%d in %ds...",
					conversation_name, e, retry_count, max_retries, backoff,
				)
				_publish_connection_event(
					user, conversation_name, "reconnecting",
					f"Connection lost. Retry {retry_count}/{max_retries} in {backoff}s...", str(e),
				)
				await asyncio.sleep(backoff)
				backoff = min(backoff * 2, max_backoff)

	await pubsub.unsubscribe(channel)
	await redis_client.aclose()
	logger.info("Connection manager stopped: conversation=%s", conversation_name)


class _ShutdownRequested(Exception):
	"""Raised when the connection manager should stop."""
	pass


async def _listen_ws(ws, user, conversation_name):
	"""Listen for inbound messages from the Processing App and route to browser.

	Two kinds of inbound messages:
	1. Custom protocol (has "type" field) - route to browser via _route_incoming_message.
	2. MCP JSON-RPC requests (has "jsonrpc" field) - dispatch to the local MCP server
	   synchronously on this loop. Sync Frappe ORM calls block briefly (10-100ms typical,
	   up to a few seconds for dry_run_changeset), which is acceptable: WS heartbeats
	   are 30s. Running in a thread executor would lose frappe.local.session.user
	   (thread-local) and frappe.local.site, breaking permission enforcement.
	"""
	from alfred_client.mcp.transport import is_mcp_message
	from alfred_client.mcp.server import handle_mcp_request

	async for raw in ws:
		try:
			message = json.loads(raw)
		except json.JSONDecodeError:
			continue

		# MCP requests from the Processing App's agents
		if is_mcp_message(message):
			try:
				response = handle_mcp_request(message)
				await ws.send(json.dumps(response))
			except Exception as e:
				logger.error("MCP dispatch failed: %s", e, exc_info=True)
				# Send a JSON-RPC error response so the caller's future resolves
				await ws.send(json.dumps({
					"jsonrpc": "2.0",
					"id": message.get("id"),
					"error": {"code": -32603, "message": f"MCP dispatch failed: {e}"},
				}))
			continue

		# Custom protocol messages → browser
		_route_incoming_message(message, user, conversation_name)

		# ACK non-ping messages
		msg_id = message.get("msg_id")
		if msg_id and message.get("type") != "ping":
			await ws.send(json.dumps({
				"msg_id": str(uuid.uuid4()),
				"type": "ack",
				"data": {"msg_id": msg_id},
			}))


async def _listen_redis(ws, pubsub, channel, redis_client, queue_key):
	"""Listen for outbound messages from Redis pub/sub and forward to WS.

	Pub/sub is used only as a wakeup notification. The actual messages are
	read from the durable Redis list (queue_key), so nothing is lost if the
	connection manager isn't subscribed when send_message() fires.
	"""
	while True:
		message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
		if message and message["type"] == "message":
			data = message["data"]
			# Check for shutdown signal
			if data == "__shutdown__" or (isinstance(data, str) and "__shutdown__" in data):
				raise _ShutdownRequested()
			# Notification received - drain the queue
			while True:
				queued = await redis_client.lpop(queue_key)
				if not queued:
					break
				await ws.send(queued)
		await asyncio.sleep(0.05)


# ── Frappe API Layer (called from browser/frontend) ──────────────

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
	# Pub/sub is just a notification - the actual message is read from the queue.
	redis_conn.publish(channel, "__notify__")


@frappe.whitelist()
def start_conversation(conversation_name):
	"""Start a WebSocket connection for a conversation.

	Each active conversation consumes one slot on the 'long' RQ worker queue
	for up to 7200s. If the user has more active conversations than the worker
	can handle, the new job will queue indefinitely - we warn the user when we
	detect the queue is already saturated.

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
	"""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()

	if not frappe.db.exists("Alfred Conversation", conversation_name):
		frappe.throw(frappe._("Conversation not found"), frappe.DoesNotExistError)

	frappe.has_permission(
		"Alfred Conversation", ptype="read", doc=conversation_name, throw=True,
	)

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

	# Soft check: count queued + started jobs on the long queue for this site.
	# If we're at/above the worker count, the new job will sit idle and the user
	# will see nothing happen. Surface that as a clear error.
	try:
		from frappe.utils.background_jobs import get_jobs
		site_jobs = get_jobs(site=frappe.local.site, queue="long")
		long_queue_jobs = sum(len(jobs) for jobs in site_jobs.values())
		# Rough heuristic: alert above 10 concurrent long-queue jobs since the
		# default bench worker count is 1-3 for the long queue.
		if long_queue_jobs > 10:
			frappe.msgprint(
				frappe._(
					"Warning: {0} long-running jobs are already active. "
					"This conversation may take longer to start - close unused "
					"conversations if it doesn't start within 30 seconds."
				).format(long_queue_jobs),
				indicator="orange",
				alert=True,
			)
	except Exception:
		pass  # Best-effort check; don't block on saturation detection

	frappe.enqueue(
		"alfred_client.api.websocket_client._connection_manager",
		conversation_name=conversation_name,
		user=conversation_owner,
		queue="long",
		timeout=7200,
	)


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
	redis_conn.publish(channel, "__notify__")

	frappe.db.set_value(
		"Alfred Conversation", conversation_name, "status", "Cancelled",
		update_modified=True,
	)
	frappe.db.commit()
