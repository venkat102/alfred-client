# Alfred Debugging Guide

## Redis Debugging

### Which Redis instance does what?

Frappe runs **two separate Redis servers**:

| Port | Config key | Purpose |
|------|-----------|---------|
| **13000** | `redis_cache` | Cache, pub/sub, **Alfred message queue** - `frappe.cache()` points here |
| **11000** | `redis_queue` | RQ background job queue (worker jobs) |

**Critical**: Alfred's message queue uses port **13000** (cache Redis). The connection manager must connect to this same instance. Using port 11000 will silently fail - messages are written to one Redis and the reader looks at another.

### Watch Redis live

Open a terminal before clicking Send:

```bash
redis-cli -p 13000 MONITOR | grep -i alfred
```

You'll see the `RPUSH` (durable queue write) and `PUBLISH` (wakeup notification) calls in real time.

### Read the queue directly

```bash
# Find all Alfred queue keys
redis-cli -p 13000 KEYS "*alfred:ws:outbound:queue*"

# Read messages in the queue (without removing them)
redis-cli -p 13000 LRANGE "_01519ca146aa2388|alfred:ws:outbound:queue:YOUR-CONV-ID" 0 -1

# Check queue depth
redis-cli -p 13000 LLEN "_01519ca146aa2388|alfred:ws:outbound:queue:YOUR-CONV-ID"
```

> **Key prefix**: Frappe's `RedisWrapper.rpush()` auto-prefixes list keys with `<db_name>|`. The actual key looks like `_01519ca146aa2388|alfred:ws:outbound:queue:<conv-id>`. Pub/sub channel names are NOT prefixed.

### Message flow through Redis

```
send_message() called by Frappe worker
    │
    ├── rpush → durable Redis list (key: <db_prefix>|alfred:ws:outbound:queue:<conv-id>)
    │
    └── publish → pub/sub channel (channel: alfred:ws:outbound:<conv-id>)
                   payload is just "__notify__" (wakeup signal, no data)
                        │
                        ▼
              _listen_redis() receives notification
                        │
                        └── lpop loop → drains the list → sends each message over WebSocket
```

## Background Job Debugging

### Long queue worker

Alfred's connection manager runs on the `long` RQ queue (timeout: 7200s / 2 hours). The Procfile must include a `worker_long` entry:

```
worker_long: OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES NO_PROXY=* bench worker --queue long 1>> logs/worker_long.log 2>> logs/worker_long.error.log
```

**Check if the long worker is running:**
```bash
ps aux | grep 'rq.*long' | grep -v grep
```

**Check for stale jobs piling up:**
```bash
redis-cli -p 11000 LLEN 'rq:queue:Users-venkatesh-bench-develop-frappe-bench:long'
# If this number keeps growing, the long worker isn't running
```

**Clear stale jobs** (only if you're sure they're all stale):
```bash
redis-cli -p 11000 DEL 'rq:queue:Users-venkatesh-bench-develop-frappe-bench:long'
```

### Check worker logs

```bash
# Long worker (connection manager runs here)
tail -f ~/bench/develop/frappe-bench/logs/worker_long.log
tail -f ~/bench/develop/frappe-bench/logs/worker_long.error.log

# Default worker
tail -f ~/bench/develop/frappe-bench/logs/worker.log
tail -f ~/bench/develop/frappe-bench/logs/worker.error.log
```

## LLM Debugging

### Test from Alfred Settings UI

Go to `/app/alfred-settings` → **Actions** dropdown:
- **Test LLM Connection** - checks Ollama reachability, model availability, and generation
- **Test Processing App** - checks the Processing App's `/health` endpoint

### Test from CLI (Processing App)

```bash
cd ~/bench/develop/alfred_processing

# Uses .env config
.venv/bin/python test_llm.py

# Override model/URL
.venv/bin/python test_llm.py --model ollama/llama3.2:3b --base-url http://localhost:11434

# Test without streaming
.venv/bin/python test_llm.py --no-stream

# Custom prompt
.venv/bin/python test_llm.py --prompt "Say hello"
```

### Common LLM errors

| Error | Cause | Fix |
|-------|-------|-----|
| `[Errno 61] Connection refused` | Ollama not running or wrong URL | Start `ollama serve`, check Base URL |
| `litellm.Timeout` | LLM unreachable or very slow | Check network, try smaller model |
| `Model not found` | Model not pulled | Run `ollama pull <model-name>` |
| `AuthenticationError` | Invalid API key (cloud providers) | Check key in Alfred Settings |

## Processing App Debugging

### Health check

```bash
curl http://localhost:8001/health
# Expected: {"status": "ok", "version": "0.1.0", "redis": "connected"}
```

### Logs (native dev)

When running via `./dev.sh`, logs stream to the terminal. Key markers to grep for:

```
# WebSocket lifecycle
alfred.websocket INFO: WebSocket connection opened: conversation=<id>
alfred.websocket INFO: WebSocket authenticated: user=..., site=dev.alfred

# Pipeline mode resolution (tells you full vs lite and why)
alfred.websocket INFO: Pipeline mode resolved for <site>: full (source=site_config)
alfred.websocket INFO: Pipeline mode resolved for <site>: lite (source=plan)

# Agent crew startup (sequential process = 6 agents, not 7 - orchestrator removed)
alfred.agents INFO: Built 6 agents with LLM: ollama/qwen2.5-coder:32b

# MCP tool round-trips (should appear many times per prompt)
alfred.mcp_client DEBUG: MCP request sent: tool=get_doctype_schema, id=<uuid>
# (handle_response logs are only visible at DEBUG level)

# Dry-run validation
alfred.websocket INFO: Pre-preview dry-run via MCP
alfred.websocket WARNING: Dry-run MCP call failed: ...
alfred.websocket ERROR: Dry-run retry failed: ...

# Pipeline concurrency lock (second prompt while first running)
alfred.websocket WARNING: Rejecting prompt: pipeline already running for user@site

# Lite pipeline starting blind (no MCP tools wired)
alfred.websocket WARNING: Lite pipeline starting without MCP tools for <site> - ...

# Empty extraction (crew produced something, we couldn't parse it)
alfred.websocket WARNING: _extract_changes: no JSON found in result ...
alfred.websocket WARNING: Pipeline completed but _extract_changes returned empty.
```

### Log markers on the Client App side

```bash
tail -f ~/bench/develop/frappe-bench/logs/worker.log | grep alfred
tail -f ~/bench/develop/frappe-bench/logs/web.log | grep alfred
```

```
# Connection manager lifecycle (in worker.log - long queue)
alfred.ws_client INFO: Connected to Processing App: conversation=<id>
alfred.ws_client INFO: Connection manager stopped: conversation=<id>

# MCP dispatch (in worker.log - session user set before handling)
alfred.mcp.server DEBUG: Tool <name> executed successfully
alfred.mcp.server ERROR: Tool <name> failed: ...

# Approve-time dry-run disagreement (the belt-and-suspenders check diverged from preview)
frappe WARNING: Dry-run disagreement for changeset <name>: preview-time valid=0, approve-time valid=1

# Approve flow
frappe INFO: approve_changeset: deploying <name> with N operation(s) for user <email>
```

### Common pitfalls

| Pitfall | Symptom | Root cause |
|---------|---------|------------|
| Wrong Redis instance | Messages queued but never drained | `websocket_client.py` must use `redis_cache` (port 13000), not `redis_queue` (port 11000) |
| Missing key prefix | Queue depth stuck at 1 | `frappe.cache().rpush()` auto-prefixes keys with `<db_name>\|` but raw `aioredis.lpop()` doesn't - the consumer must include the prefix |
| site_id has special chars | `site_id contains invalid characters` | `_get_site_id()` must return bare site name (`dev.alfred`), not full URL (`http://dev.alfred:8000`) |
| No long worker | Jobs pile up, nothing executes | Procfile needs `worker_long` entry with `--queue long` |
| Pub/sub message lost | Message sent but connection manager missed it | Fixed: messages are now durably queued (rpush) and pub/sub is just a wakeup notification |
| MCP tool returns "too many records" when user shouldn't see them | Permission filter bypassed | `_connection_manager` must call `frappe.set_user(user)` at start; the finally block restores it. Without this, all MCP calls run as Administrator. |
| MCP calls hang for 30s then TimeoutError | Cross-loop future resolution broken | `MCPClient` must be constructed with `main_loop=asyncio.get_running_loop()` in the WS handler; see `alfred/api/websocket.py:_authenticate_handshake` |
| "Basic" badge stuck on, can't switch | Plan override active | Check admin portal's `check_plan` response - if it returns `pipeline_mode=lite`, the site setting is overridden. Look for `source=plan` in the Pipeline mode resolved log line. |
| Preview shows old changeset after sending new prompt | Polling/realtime race | The UI's `currentPromptSentAt` cutoff should reject this; if it still happens, check that `get_latest_changeset` / `get_changeset` return `creation` field |
| Activity ticker stuck showing last tool call | Pipeline timed out without sending completion | Client-side stuck timeout (10 min) should recover; check for "Pipeline stalled" in the chat |
