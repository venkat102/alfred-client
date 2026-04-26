"""Module-level constants for the websocket_client package.

These are read by tests and by the page module (alfred_chat.py), so
they're re-exported from ``alfred_client.api.websocket_client`` (the
package init) to keep their import paths stable across the package
split.
"""

from __future__ import annotations

# Redis channel prefix for message passing between Frappe workers and the
# connection manager. Used as both a pub/sub channel and a list-key
# namespace; downstream code appends ``<conversation_name>`` for the
# pub/sub channel and ``queue:<conversation_name>`` for the durable list.
_REDIS_CHANNEL_PREFIX = "alfred:ws:outbound:"

# Cap on the durable disconnected-session queue per conversation. Every
# rpush into ``<prefix>queue:<conv>`` is followed by an LTRIM that keeps
# only the last N entries, so a 24h+ disconnect (closed laptop lid,
# stale browser tab, dead worker) cannot grow the list without bound.
# The connection manager drains this list on (re)connect, so under
# normal use the cap is never hit - it's an upper bound on the worst
# case where the consumer is gone for a long time. 10k messages at ~1KB
# each = ~10MB per conversation, comfortably within the cache-Redis
# memory budget for typical fleets.
#
# This cap MUST be applied at every rpush call site - a missed site
# silently re-introduces the unbounded-growth bug.
_DISCONNECTED_QUEUE_MAX_LEN = 10_000

# TTL for the last_msg_id tracker. Matches the processing app's 7-day
# event-stream TTL (alfred_processing/alfred/state/store.py), because a
# resume anchor is only useful for as long as the server still has the
# events to replay.
_LAST_MSG_ID_TTL_SECONDS = 7 * 24 * 3600

# Heartbeat freshness window for _long_queue_worker_count. RQ's default
# heartbeat interval is 15s; a worker that has missed > 4 beats is almost
# certainly dead. Keeping the window generous avoids false-positive dead
# calls on a hung Redis connection.
_WORKER_HEARTBEAT_STALE_SECONDS = 90
