"""Persistent WebSocket client connecting the Client App to the Processing App.

Opens an outbound WebSocket connection to the Processing App URL configured in
Alfred Settings. Handles authentication, message routing, reconnection with
exponential backoff, and message replay after disconnection.

Architecture:
	Browser <-> Frappe (Socket.IO) <-> This WebSocket Client <-> Processing App
"""

import asyncio
import json
import logging
import time
import uuid

import frappe
import jwt as pyjwt
import websockets
from websockets.exceptions import (
	ConnectionClosed,
	ConnectionClosedError,
	InvalidStatusCode,
)

logger = logging.getLogger("alfred.ws_client")


def _get_site_id():
	"""Get the canonical site_id — the Frappe site URL."""
	return frappe.utils.get_url()


def _generate_jwt(api_key, user=None, roles=None):
	"""Generate a signed JWT for WebSocket handshake.

	Args:
		api_key: The shared secret from Alfred Settings.
		user: User email. Defaults to current session user.
		roles: User roles. Defaults to current user's roles.

	Returns:
		Signed JWT token string.
	"""
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
		"exp": now + 86400,  # 24 hours
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


class AlfredWebSocketClient:
	"""Manages a single WebSocket connection per conversation to the Processing App.

	Handles:
	- Authenticated handshake with JWT and site config
	- Message routing (incoming from Processing App -> Frappe Socket.IO)
	- Reconnection with exponential backoff
	- Message replay after reconnection
	"""

	# Exponential backoff: 1s, 2s, 4s, 8s, 16s, 32s, 60s cap
	MIN_BACKOFF = 1
	MAX_BACKOFF = 60

	def __init__(self, conversation_name, user=None):
		self.conversation_name = conversation_name
		self.user = user or frappe.session.user
		self.ws = None
		self.connected = False
		self.last_msg_id = None
		self._backoff = self.MIN_BACKOFF
		self._should_stop = False

	def _get_processing_url(self):
		"""Build the WebSocket URL from Alfred Settings."""
		settings = frappe.get_single("Alfred Settings")
		base_url = settings.processing_app_url
		if not base_url:
			raise ValueError("Alfred Settings: processing_app_url is not configured")
		# Ensure ws:// or wss:// prefix
		if base_url.startswith("http://"):
			base_url = "ws://" + base_url[7:]
		elif base_url.startswith("https://"):
			base_url = "wss://" + base_url[8:]
		elif not base_url.startswith(("ws://", "wss://")):
			base_url = "wss://" + base_url
		return f"{base_url.rstrip('/')}/ws/{self.conversation_name}"

	async def connect(self):
		"""Establish WebSocket connection with authenticated handshake."""
		url = self._get_processing_url()
		settings = frappe.get_single("Alfred Settings")
		api_key = settings.get_password("api_key")

		if not api_key:
			raise ValueError("Alfred Settings: api_key is not configured")

		logger.info("Connecting to Processing App: %s", url)

		self.ws = await websockets.connect(
			url,
			ping_interval=30,
			ping_timeout=10,
			close_timeout=5,
		)

		# Send handshake
		handshake = {
			"api_key": api_key,
			"jwt_token": _generate_jwt(api_key, self.user),
			"site_config": _get_site_config(),
		}
		await self.ws.send(json.dumps(handshake))

		# Wait for auth confirmation
		response = json.loads(await self.ws.recv())
		if response.get("type") == "auth_success":
			self.connected = True
			self._backoff = self.MIN_BACKOFF
			logger.info(
				"Connected to Processing App: user=%s, site=%s, conversation=%s",
				self.user, response["data"].get("site_id"), self.conversation_name,
			)

			# If reconnecting, request replay of missed messages
			if self.last_msg_id:
				await self.ws.send(json.dumps({
					"msg_id": str(uuid.uuid4()),
					"type": "resume",
					"data": {"last_msg_id": self.last_msg_id},
				}))
		else:
			raise ConnectionError(f"Handshake failed: {response}")

	async def send_message(self, msg_type, data):
		"""Send a message to the Processing App.

		Args:
			msg_type: Message type (e.g., 'prompt', 'user_response', 'deploy_command').
			data: Message data dict.
		"""
		if not self.connected or not self.ws:
			raise ConnectionError("Not connected to Processing App")

		msg = {
			"msg_id": str(uuid.uuid4()),
			"type": msg_type,
			"data": data,
		}
		await self.ws.send(json.dumps(msg))
		return msg["msg_id"]

	async def _send_ack(self, msg_id):
		"""Acknowledge receipt of a message from the Processing App."""
		if self.ws:
			await self.ws.send(json.dumps({
				"msg_id": str(uuid.uuid4()),
				"type": "ack",
				"data": {"msg_id": msg_id},
			}))

	def _route_incoming_message(self, message):
		"""Route an incoming message from the Processing App to the appropriate handler.

		Publishes events to the browser via Frappe's Socket.IO.
		"""
		msg_type = message.get("type", "")
		msg_id = message.get("msg_id", "")
		data = message.get("data", {})

		if msg_type == "ping":
			return  # Heartbeat, no action needed

		# Track last received message for replay on reconnect
		if msg_id:
			self.last_msg_id = msg_id

		if msg_type == "agent_status":
			frappe.publish_realtime(
				"intern_agent_status",
				data,
				user=self.user,
				after_commit=False,
			)
		elif msg_type == "question":
			frappe.publish_realtime(
				"intern_question",
				data,
				user=self.user,
				after_commit=False,
			)
		elif msg_type in ("preview", "changeset"):
			# Store changeset in database
			self._store_changeset(data)
			frappe.publish_realtime(
				"intern_preview",
				data,
				user=self.user,
				after_commit=False,
			)
		elif msg_type == "error":
			frappe.publish_realtime(
				"intern_error",
				data,
				user=self.user,
				after_commit=False,
			)
		elif msg_type == "echo":
			# Echo response for testing
			frappe.publish_realtime(
				"intern_agent_status",
				{"type": "echo", **data},
				user=self.user,
				after_commit=False,
			)
		elif msg_type == "mcp_request":
			# MCP tool execution request — forward to MCP server (Task 2.3)
			frappe.publish_realtime(
				"intern_mcp_request",
				data,
				user=self.user,
				after_commit=False,
			)
		elif msg_type == "deploy":
			# Deployment command — execute via deployment engine (Task 4.1)
			frappe.publish_realtime(
				"intern_deploy",
				data,
				user=self.user,
				after_commit=False,
			)
		else:
			logger.debug("Unknown message type from Processing App: %s", msg_type)

	def _store_changeset(self, data):
		"""Store a changeset preview in the database."""
		try:
			changeset = frappe.get_doc({
				"doctype": "Alfred Changeset",
				"conversation": self.conversation_name,
				"status": "Pending",
				"changes": json.dumps(data.get("changes", [])),
			})
			changeset.insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception as e:
			logger.error("Failed to store changeset: %s", e)

	async def listen(self):
		"""Listen for incoming messages and route them. Handles reconnection."""
		while not self._should_stop:
			try:
				if not self.connected:
					await self.connect()

				async for raw_message in self.ws:
					try:
						message = json.loads(raw_message)
					except json.JSONDecodeError:
						logger.warning("Received invalid JSON from Processing App")
						continue

					self._route_incoming_message(message)

					# Acknowledge receipt
					msg_id = message.get("msg_id")
					if msg_id and message.get("type") != "ping":
						await self._send_ack(msg_id)

			except (ConnectionClosed, ConnectionClosedError) as e:
				self.connected = False
				logger.warning(
					"Connection lost (conversation=%s): %s. Reconnecting in %ds...",
					self.conversation_name, e, self._backoff,
				)
			except ConnectionRefusedError:
				self.connected = False
				logger.warning(
					"Connection refused (conversation=%s). Reconnecting in %ds...",
					self.conversation_name, self._backoff,
				)
			except Exception as e:
				self.connected = False
				logger.error(
					"WebSocket error (conversation=%s): %s. Reconnecting in %ds...",
					self.conversation_name, e, self._backoff,
				)

			if not self._should_stop:
				await asyncio.sleep(self._backoff)
				self._backoff = min(self._backoff * 2, self.MAX_BACKOFF)

	async def disconnect(self):
		"""Gracefully close the WebSocket connection."""
		self._should_stop = True
		if self.ws:
			await self.ws.close()
			self.connected = False
			logger.info("Disconnected from Processing App: conversation=%s", self.conversation_name)


# ── Frappe API Layer ─────────────────────────────────────────────
# These are the whitelisted functions called from the browser/frontend.

# Active connections per conversation
_active_connections: dict[str, AlfredWebSocketClient] = {}


@frappe.whitelist()
def send_message(conversation_name, message, msg_type="prompt"):
	"""Send a message from the browser to the Processing App.

	Called by the Chat UI when the user sends a prompt or responds to a question.

	Args:
		conversation_name: The Alfred Conversation name.
		message: Message text or data.
		msg_type: Message type (prompt, user_response, deploy_command).
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	# Store the message in the database
	msg_doc = frappe.get_doc({
		"doctype": "Alfred Message",
		"conversation": conversation_name,
		"role": "user",
		"message_type": "text",
		"content": message,
	})
	msg_doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# Send via WebSocket (async bridge)
	frappe.enqueue(
		"alfred_client.api.websocket_client._async_send_message",
		conversation_name=conversation_name,
		msg_type=msg_type,
		data={"text": message, "user": frappe.session.user},
		queue="long",
	)


@frappe.whitelist()
def start_conversation(conversation_name):
	"""Start a new WebSocket connection for a conversation.

	Called when the user opens the chat UI and begins a conversation.
	"""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	# Validate conversation exists
	if not frappe.db.exists("Alfred Conversation", conversation_name):
		frappe.throw(frappe._("Conversation not found"), frappe.DoesNotExistError)

	# Start the WebSocket connection in a background job
	frappe.enqueue(
		"alfred_client.api.websocket_client._async_start_connection",
		conversation_name=conversation_name,
		user=frappe.session.user,
		queue="long",
		timeout=3600,  # 1 hour max
	)


@frappe.whitelist()
def stop_conversation(conversation_name):
	"""Stop the WebSocket connection for a conversation."""
	from alfred_client.api.permissions import validate_alfred_access

	validate_alfred_access()

	frappe.enqueue(
		"alfred_client.api.websocket_client._async_stop_connection",
		conversation_name=conversation_name,
		queue="long",
	)


def _async_start_connection(conversation_name, user):
	"""Background job: start a WebSocket connection for a conversation."""
	import asyncio

	client = AlfredWebSocketClient(conversation_name, user)
	_active_connections[conversation_name] = client

	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)
	try:
		loop.run_until_complete(client.listen())
	except Exception as e:
		logger.error("Connection failed for conversation %s: %s", conversation_name, e)
		frappe.publish_realtime(
			"intern_error",
			{
				"conversation": conversation_name,
				"error": str(e),
				"message": "Failed to connect to Processing App. Check Alfred Settings.",
			},
			user=user,
		)
	finally:
		_active_connections.pop(conversation_name, None)
		loop.close()


def _async_stop_connection(conversation_name):
	"""Background job: stop a WebSocket connection."""
	import asyncio

	client = _active_connections.get(conversation_name)
	if client:
		loop = asyncio.new_event_loop()
		asyncio.set_event_loop(loop)
		try:
			loop.run_until_complete(client.disconnect())
		finally:
			_active_connections.pop(conversation_name, None)
			loop.close()


def _async_send_message(conversation_name, msg_type, data):
	"""Background job: send a message via the WebSocket."""
	import asyncio

	client = _active_connections.get(conversation_name)
	if not client:
		logger.warning("No active connection for conversation %s", conversation_name)

		# Notify user that connection is not active
		frappe.publish_realtime(
			"intern_error",
			{
				"conversation": conversation_name,
				"error": "Not connected",
				"message": "Connection to Processing App is not active. Please restart the conversation.",
			},
			user=data.get("user", frappe.session.user),
		)
		return

	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)
	try:
		loop.run_until_complete(client.send_message(msg_type, data))
	except Exception as e:
		logger.error("Failed to send message for conversation %s: %s", conversation_name, e)
		frappe.publish_realtime(
			"intern_error",
			{
				"conversation": conversation_name,
				"error": str(e),
				"message": "Failed to send message to Processing App.",
			},
			user=data.get("user", frappe.session.user),
		)
	finally:
		loop.close()
