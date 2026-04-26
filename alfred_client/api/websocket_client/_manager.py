"""Long-running connection-manager: the asyncio core of the websocket_client.

This is the entry point for the RQ ``long`` queue job that owns one
WebSocket to the alfred_processing app for the duration of a
conversation. It runs an asyncio event loop with two concurrent tasks:

  1. ``_listen_ws`` - reads frames from the processing app, dispatches
     MCP JSON-RPC requests synchronously, routes custom-protocol frames
     to the browser via ``_routing._route_incoming_message``.
  2. ``_listen_redis`` - drains the durable Redis queue + listens for
     pub/sub notifications + watches for the ``__shutdown__`` sentinel.

The manager handles reconnect with exponential backoff (1s → 60s, 10
tries), restores ``frappe.session.user`` to the conversation owner so
MCP tool dispatch sees the right permissions, and self-exits at a
lifetime cap (6300s by default, just under RQ's 7200s force-kill).

The RQ enqueue site at ``_endpoints.start_conversation`` hardcodes
``"alfred_client.api.websocket_client._connection_manager"`` as the
method string. The package's ``__init__.py`` re-exports
``_connection_manager`` so that dotted path stays resolvable for in-
flight queued jobs that were enqueued before the package split.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import frappe

from alfred_client.api.websocket_client._auth import _generate_jwt, _get_site_config
from alfred_client.api.websocket_client._cache import _load_last_msg_id
from alfred_client.api.websocket_client._constants import _REDIS_CHANNEL_PREFIX
from alfred_client.api.websocket_client._routing import _route_incoming_message

logger = logging.getLogger("alfred.ws_client")


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
	# last_msg_id is read from Frappe cache each handshake iteration (not
	# once at init) so a genuine reconnect picks up events that landed
	# while this manager was backing off. _route_incoming_message bumps
	# it via _track_last_msg_id on every user-visible incoming event.
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

				# Resume if reconnecting: read the last msg_id the UI saw
				# and ask the server to replay events after it. First
				# connect on a fresh conversation has no cached value,
				# so this sends nothing (correct - nothing to replay).
				last_msg_id = _load_last_msg_id(conversation_name)
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
	from alfred_client.mcp.server import handle_mcp_request
	from alfred_client.mcp.transport import is_mcp_message

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
