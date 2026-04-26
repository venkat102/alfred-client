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

Package layout (TD-L1 split, mirrors alfred_processing's TD-H2 pipeline split):

  - ``_constants``      module-level constants (channel prefix, queue cap, TTLs)
  - ``_cache``          last_msg_id resume cursor, frappe.cache() RW
  - ``_auth``           site_id, JWT minting, site_config snapshot
  - ``_routing``        inbound WS frame -> browser realtime + persistence
  - ``_introspection``  RQ worker + job introspection used by start_conversation
  - ``_manager``        long-running connection_manager job + asyncio loop
  - ``_endpoints``      Frappe-whitelisted RPC handlers (the public surface)

Dependency graph (no cycles):

  _constants  (leaf)
  _cache      -> _constants
  _auth       (leaf)
  _routing    -> _cache
  _introspection -> _constants
  _manager    -> _constants, _auth, _cache, _routing
  _endpoints  -> _constants, _introspection

CRITICAL: every public name listed in ``__all__`` MUST stay reachable
at ``alfred_client.api.websocket_client.<name>`` because:

  - Frappe RPC: the ``@frappe.whitelist()`` endpoints in ``_endpoints``
    are addressed by callers via the package-root dotted path (Vue
    ``frappe.call``, integrations).
  - RQ in-flight jobs: the enqueue site at
    ``_endpoints.start_conversation`` hardcodes
    ``"alfred_client.api.websocket_client._connection_manager"`` as the
    method string; jobs already in Redis store this verbatim. The
    re-export below keeps the path resolving for those jobs.
  - Tests: ``test_websocket_client.py`` and others import constants and
    helpers from the package root, not from the submodule paths.
  - Page module: ``alfred_settings/page/alfred_chat/alfred_chat.py``
    imports ``_REDIS_CHANNEL_PREFIX``, ``_DISCONNECTED_QUEUE_MAX_LEN``,
    and ``start_conversation`` from the package root.

Adding a new public name: place the definition in the appropriate
submodule, then re-export here AND add to ``__all__``.
"""

from __future__ import annotations

# ── Constants ─────────────────────────────────────────────────────────
from alfred_client.api.websocket_client._constants import (
	_DISCONNECTED_QUEUE_MAX_LEN,
	_LAST_MSG_ID_TTL_SECONDS,
	_REDIS_CHANNEL_PREFIX,
	_WORKER_HEARTBEAT_STALE_SECONDS,
)

# ── Resume-cursor cache ───────────────────────────────────────────────
from alfred_client.api.websocket_client._cache import (
	_last_msg_id_key,
	_load_last_msg_id,
	_track_last_msg_id,
)

# ── Identity helpers (used by tests + the manager handshake) ──────────
from alfred_client.api.websocket_client._auth import (
	_generate_jwt,
	_get_site_config,
	_get_site_id,
)

# ── Inbound routing + persistence ─────────────────────────────────────
from alfred_client.api.websocket_client._routing import (
	_RUN_TERMINAL_TYPES,
	_route_incoming_message,
	_store_agent_reply_message,
	_store_plan_doc_message,
	_update_conversation_run_state,
)

# ── RQ introspection (workers + jobs) ─────────────────────────────────
from alfred_client.api.websocket_client._introspection import (
	_conversation_job_in_flight,
	_long_queue_worker_count,
)

# ── Connection-manager (RQ entry point) ───────────────────────────────
# Re-exporting _connection_manager from the package root is LOAD-BEARING:
# the enqueue site stores the method string
# "alfred_client.api.websocket_client._connection_manager" verbatim into
# Redis. Any in-flight job with that string MUST find the function at
# this path. Removing this re-export silently breaks all queued jobs.
from alfred_client.api.websocket_client._manager import (
	_connection_loop,
	_connection_manager,
	_listen_redis,
	_listen_ws,
	_publish_connection_event,
	_reconnect_db_if_stale,
	_ShutdownRequested,
)

# ── Frappe-whitelisted endpoints (the public RPC surface) ─────────────
from alfred_client.api.websocket_client._endpoints import (
	cancel_run,
	send_message,
	start_conversation,
	stop_conversation,
)

__all__ = [
	# constants
	"_DISCONNECTED_QUEUE_MAX_LEN",
	"_LAST_MSG_ID_TTL_SECONDS",
	"_REDIS_CHANNEL_PREFIX",
	"_WORKER_HEARTBEAT_STALE_SECONDS",
	# cache
	"_last_msg_id_key",
	"_load_last_msg_id",
	"_track_last_msg_id",
	# auth
	"_generate_jwt",
	"_get_site_config",
	"_get_site_id",
	# routing
	"_RUN_TERMINAL_TYPES",
	"_route_incoming_message",
	"_store_agent_reply_message",
	"_store_plan_doc_message",
	"_update_conversation_run_state",
	# introspection
	"_conversation_job_in_flight",
	"_long_queue_worker_count",
	# manager
	"_ShutdownRequested",
	"_connection_loop",
	"_connection_manager",
	"_listen_redis",
	"_listen_ws",
	"_publish_connection_event",
	"_reconnect_db_if_stale",
	# whitelist endpoints
	"cancel_run",
	"send_message",
	"start_conversation",
	"stop_conversation",
]
