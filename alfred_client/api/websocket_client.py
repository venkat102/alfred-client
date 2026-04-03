"""Persistent WebSocket client connecting the Client App to the Processing App.

Architecture:
  Browser <-> Frappe (Socket.IO) <-> This module <-> Processing App (WebSocket)

Message flow:
  - Outbound: send_message() publishes to Redis channel -> connection manager picks up -> sends via WS
  - Inbound: WS message arrives -> connection manager routes -> frappe.publish_realtime() to browser

The connection manager runs as a single long-running background job per conversation.
Messages are passed via Redis pub/sub to decouple senders from the event loop that
holds the WebSocket connection. This avoids the broken pattern of creating a new
asyncio event loop per frappe.enqueue call.
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
	"""Get the canonical site_id - the Frappe site URL."""
	return frappe.utils.get_url()


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

	event_map = {
		"agent_status": "alfred_agent_status",
		"question": "alfred_question",
		"preview": "alfred_preview",
		"changeset": "alfred_preview",
		"error": "alfred_error",
		"echo": "alfred_agent_status",
		"mcp_request": "alfred_mcp_request",
		"deploy": "alfred_deploy",
		"auth_success": None,
	}

	event_name = event_map.get(msg_type)
	if event_name:
		frappe.publish_realtime(event_name, data, user=user, after_commit=False)

	# Store changeset previews in the database
	if msg_type in ("preview", "changeset") and data.get("changes"):
		try:
			changeset = frappe.get_doc({
				"doctype": "Alfred Changeset",
				"conversation": conversation_name,
				"status": "Pending",
				"changes": json.dumps(data.get("changes", [])),
			})
			changeset.insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception as e:
			logger.error("Failed to store changeset: %s", e)


def _connection_manager(conversation_name, user):
	"""Long-running background job: manages a single WebSocket connection.

	Runs one asyncio event loop that:
	1. Opens a WebSocket to the Processing App with authenticated handshake
	2. Subscribes to a Redis pub/sub channel for outbound messages
	3. Routes inbound WS messages to the browser via frappe.publish_realtime()
	4. Handles reconnection with exponential backoff

	All message sending goes through Redis pub/sub - no cross-loop issues.
	"""
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)
	try:
		loop.run_until_complete(_connection_loop(conversation_name, user))
	except Exception as e:
		logger.error("Connection manager died for %s: %s", conversation_name, e)
		frappe.publish_realtime(
			"alfred_error",
			{"conversation": conversation_name, "error": str(e),
			 "message": "Connection to Processing App failed. Check Alfred Settings."},
			user=user,
		)
	finally:
		loop.close()


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

	# Connect to the bench's Redis for pub/sub (outbound message channel)
	redis_url = frappe.conf.get("redis_queue") or "redis://localhost:11000"
	redis_client = aioredis.from_url(redis_url, decode_responses=True)
	pubsub = redis_client.pubsub()
	channel = f"{_REDIS_CHANNEL_PREFIX}{conversation_name}"
	await pubsub.subscribe(channel)

	backoff = 1
	max_backoff = 60
	last_msg_id = None
	should_stop = False

	while not should_stop:
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
				logger.info("Connected to Processing App: conversation=%s", conversation_name)

				# Resume if reconnecting
				if last_msg_id:
					await ws.send(json.dumps({
						"msg_id": str(uuid.uuid4()),
						"type": "resume",
						"data": {"last_msg_id": last_msg_id},
					}))

				# Run two tasks concurrently:
				# 1. Listen for inbound WS messages (from Processing App)
				# 2. Listen for outbound Redis pub/sub messages (from Frappe workers)
				ws_listen = asyncio.create_task(_listen_ws(ws, user, conversation_name))
				redis_listen = asyncio.create_task(_listen_redis(ws, pubsub, channel))

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
			logger.warning(
				"Connection lost (conversation=%s): %s. Reconnecting in %ds...",
				conversation_name, e, backoff,
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
	"""Listen for inbound messages from the Processing App and route to browser."""
	async for raw in ws:
		try:
			message = json.loads(raw)
		except json.JSONDecodeError:
			continue

		_route_incoming_message(message, user, conversation_name)

		# ACK non-ping messages
		msg_id = message.get("msg_id")
		if msg_id and message.get("type") != "ping":
			await ws.send(json.dumps({
				"msg_id": str(uuid.uuid4()),
				"type": "ack",
				"data": {"msg_id": msg_id},
			}))


async def _listen_redis(ws, pubsub, channel):
	"""Listen for outbound messages from Redis pub/sub and forward to WS."""
	while True:
		message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
		if message and message["type"] == "message":
			data = message["data"]
			# Check for shutdown signal
			try:
				parsed = json.loads(data)
				if parsed.get("type") == "__shutdown__":
					raise _ShutdownRequested()
			except (json.JSONDecodeError, _ShutdownRequested):
				if isinstance(data, str) and "__shutdown__" in data:
					raise _ShutdownRequested()
				raise
			await ws.send(data)
		await asyncio.sleep(0.05)


# ── Frappe API Layer (called from browser/frontend) ──────────────

@frappe.whitelist()
def send_message(conversation_name, message, msg_type="prompt"):
	"""Send a message to the Processing App via Redis pub/sub.

	This is safe to call from any Frappe worker - it publishes to a Redis
	channel, and the connection manager (running in its own event loop)
	picks it up and forwards it over the WebSocket.
	"""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()

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
	redis_conn.publish(channel, msg)


@frappe.whitelist()
def start_conversation(conversation_name):
	"""Start a WebSocket connection for a conversation."""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()

	if not frappe.db.exists("Alfred Conversation", conversation_name):
		frappe.throw(frappe._("Conversation not found"), frappe.DoesNotExistError)

	frappe.enqueue(
		"alfred_client.api.websocket_client._connection_manager",
		conversation_name=conversation_name,
		user=frappe.session.user,
		queue="long",
		timeout=7200,
	)


@frappe.whitelist()
def stop_conversation(conversation_name):
	"""Stop the WebSocket connection for a conversation."""
	from alfred_client.api.permissions import validate_alfred_access
	validate_alfred_access()

	redis_conn = frappe.cache()
	channel = f"{_REDIS_CHANNEL_PREFIX}{conversation_name}"
	redis_conn.publish(channel, json.dumps({"type": "__shutdown__"}))
