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

### Metrics scrape

```bash
curl -s http://localhost:8001/metrics | grep alfred_
```

See `operations.md` for the six Prometheus metrics and alerting
suggestions. Useful for diagnosing "which phase is slow" - filter
for `alfred_pipeline_phase_duration_seconds_bucket{phase="..."}`.

### FKB retrieval benchmark

When debugging bad FKB hits (the agent reads the wrong rule / idiom /
API), run the benchmark to see how keyword vs semantic retrieval
scores against a fixed query set:

```bash
cd alfred_processing
.venv/bin/python tools/fkb_benchmark.py
```

Output shows MRR, hit@1/3/5, and per-query latency for keyword,
semantic, and hybrid paths. If you see a new regression, a KB edit
likely changed embeddings or keyword weights.

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
| MCP tool returns "too many records" when user shouldn't see them | Permission filter bypassed | `_connection_manager` must call `frappe.set_user(conversation_owner)` at start; the finally block restores it. Without this, all MCP calls run as Administrator. The owner is loaded from `Alfred Conversation` inside `start_conversation`, NOT copied from the caller's session - otherwise a shared conversation would run with the wrong permissions. |
| MCP calls hang for 30s then TimeoutError | Cross-loop future resolution broken | `MCPClient` must be constructed with `main_loop=asyncio.get_running_loop()` in the WS handler; see `alfred/api/websocket.py:_authenticate_handshake` |
| "Basic" badge stuck on, can't switch | Plan override active | Check admin portal's `check_plan` response - if it returns `pipeline_mode=lite`, the site setting is overridden. Look for `source=plan` in the Pipeline mode resolved log line. |
| `pipeline_mode` always `null` in check_plan response | Plan DocType missing the field | Run `bench migrate` on the admin site - the `pipeline_mode` field is a recent addition to `Alfred Plan`. Existing plans keep the default `full` until you change them. |
| Workflow "deployed" during dry-run (shouldn't commit) | Legacy dry-run path calling `.insert()` on DDL doctypes | Fixed: `_dry_run_single` now routes DDL-triggering doctypes through `_meta_check_only` which never calls `.insert()`. If you're on an older build, rerun `bench migrate` and verify `_DDL_TRIGGERING_DOCTYPES` exists in `alfred_client/api/deploy.py`. |
| Preview shows old changeset after sending new prompt | Polling/realtime race | The UI's `currentPromptSentAt` cutoff should reject this; if it still happens, check that `get_latest_changeset` / `get_changeset` return `creation` field |
| Activity ticker stuck showing last tool call | Pipeline timed out without sending completion | Client-side stuck timeout (10 min) should recover; check for "Pipeline stalled" in the chat |
| Developer emits 5 repeated JSON arrays with `<|im_start|>` tokens | qwen retry loop during dry-run self-heal | `_extract_changes` now uses `JSONDecoder.raw_decode` to pick the first well-formed block and strips chat-template leakage; the retry task has `developer.max_iter = 3`. Both should land together - check `_CHAT_TEMPLATE_LEAKAGE` regex exists in `alfred/api/websocket.py`. |

## Pipeline Tracing (Phase 3 #14)

When pipeline runs need post-mortem analysis, enable structured span tracing
on the processing app:

```bash
export ALFRED_TRACING_ENABLED=1
export ALFRED_TRACE_PATH=./alfred_trace.jsonl
export ALFRED_TRACE_STDOUT=1   # optional - echoes a summary to stderr
./dev.sh   # or restart the docker container
```

Each pipeline phase emits one JSON span on completion:

```json
{
  "name": "pipeline.run_crew",
  "trace_id": "a1b2c3...",
  "span_id": "d4e5f6...",
  "parent_id": null,
  "start": 1712947200.123,
  "end": 1712947460.789,
  "duration_s": 260.666,
  "status": "ok",
  "error": null,
  "attrs": {"pipeline_mode": "full", "tasks": 6, "status": "completed"},
  "events": [],
  "conversation_id": "conv-abc123"
}
```

Span names:

- `pipeline.sanitize` - prompt defense
- `pipeline.load_state` - Redis + conversation memory load
- `pipeline.warmup` - pre-warm Ollama models when multi-tier is configured.
    No-op with a single model. Attrs: `warmed_models` (list of model names loaded).
- `pipeline.plan_check` - admin portal plan query
- `pipeline.orchestrate` - three-mode chat orchestrator (Phase A). Decides dev/plan/insights/chat.
- `pipeline.enhance` - prompt enhancer LLM call (skipped when mode != dev)
- `pipeline.clarify` - clarification gate (skipped when mode != dev)
- `pipeline.inject_kb` - hybrid FKB retrieval + site reconnaissance. Attrs:
    `injected_kb`         list of FKB entry ids prepended to the Developer task
    `injected_count`      count of FKB hits (`len(injected_kb)`)
    `injected_site_doctypes`  list of DocTypes whose site state was injected
    `injected_site_count` total artefact count across all injected DocTypes
    `banner_chars`        size of the prepended banner in characters
    `skipped`             "not-dev-mode" | "stop-signal" | "empty-prompt" (if gated)
    `fkb_error` / `error` error class if FKB retrieval or response shape failed
- `pipeline.resolve_mode` - full vs lite selection (skipped when mode != dev)
- `pipeline.build_crew` - crew + MCP tool setup (skipped when mode != dev)
- `pipeline.run_crew` - CrewAI kickoff, usually the longest (skipped when mode != dev)
- `pipeline.post_crew` - extraction, rescue, reflect, dry-run (skipped when mode != dev)

### Query recipes

```bash
# Total pipeline time per conversation
jq -s 'group_by(.conversation_id) | map({conv: .[0].conversation_id,
       total: map(.duration_s) | add})' alfred_trace.jsonl

# Average time per phase
jq -s 'group_by(.name) | map({phase: .[0].name,
       avg_s: (map(.duration_s) | add / length),
       runs: length})' alfred_trace.jsonl

# Only errored spans
jq 'select(.status == "error")' alfred_trace.jsonl

# All phases for a specific conversation (reconstruct the pipeline)
jq 'select(.conversation_id == "conv-abc123")' alfred_trace.jsonl
```

Tracing is zero-cost when disabled (the context manager yields a no-op Span).
Enable only when you need the data - a long-running production system
accumulates a lot of JSONL.

## Triaging "the agent still got it wrong"

When a generated changeset violates a rule you think should have been
enforced, the question is: **was the rule injected into the turn and
ignored, or never injected in the first place?** The `inject_kb` span
answers both.

```bash
# Pull inject_kb spans for a conversation and see what landed
jq 'select(.name == "pipeline.inject_kb" and
           .conversation_id == "conv-abc123") | .attrs' alfred_trace.jsonl
```

Expected shape on a working turn:

```json
{
  "injected_kb": ["server_script_no_imports", "minimal_change_principle"],
  "injected_count": 2,
  "injected_site_doctypes": ["Employee"],
  "injected_site_count": 3,
  "banner_chars": 1840
}
```

Decision matrix:

- **`injected_kb` contains the rule** but the agent violated it anyway
  -> prompt adherence problem. Check the agent's Final Answer in the
  run_crew span and the raw crew log. Adjust the rule body to be more
  emphatic, or add a style.yaml entry reinforcing the point, or raise the
  rule's keyword weights.
- **`injected_kb` doesn't contain the rule** -> retrieval miss. Look at
  the user's prompt + the rule's `keywords` list. If the prompt doesn't
  contain any of the listed keywords, either add synonyms to the rule's
  `keywords` field or lean on semantic retrieval (lower
  `semantic_min_similarity`).
- **`injected_site_doctypes` is empty for a prompt that named a DocType**
  -> target extraction miss. Walk through `_extract_target_doctypes` on the
  prompt text; likely the DocType got filtered by `_NON_DOCTYPE_CAPITALIZED`
  or the single-word 6-char threshold. Rename the constant list or adjust
  the threshold.
- **`skipped` is set** -> the phase didn't run. The value tells you why
  (non-dev mode, stop signal already set, empty prompt, no MCP client for
  the site-recon half).

The same data is logged at INFO level:

```
alfred.pipeline INFO: inject_kb: FKB=['server_script_no_imports',
  'minimal_change_principle'] site=['Employee'] for conversation=conv-abc123
```

To force a specific entry to inject for debugging, call the retrieval tool
directly on the processing app Python REPL:

```python
from alfred.knowledge import fkb
fkb.search_hybrid("validate employee age with import", k=3)
# -> [{"id": "server_script_no_imports", "_mode": "keyword", "_score": 42, ...}, ...]
```

## Orchestrator decisions (Phase A three-mode chat)

When `ALFRED_ORCHESTRATOR_ENABLED=1`, every prompt gets classified into
`dev`/`plan`/`insights`/`chat`. The decision is logged at INFO level and
also emitted as a `mode_switch` WebSocket message to the UI. Look for:

```
alfred.pipeline INFO: Orchestrator decision for conversation=conv-abc123:
  mode=chat source=fast_path confidence=high reason='Fast-path match (chat)'
```

`source` tells you how the decision was made:

- `override` - user manually forced a mode via the UI switcher (Phase D);
  LLM call was skipped.
- `fast_path` - matched a static rule (exact greeting or imperative build
  verb). LLM call was skipped. Cheapest path.
- `classifier` - LLM classification call returned a confident answer.
- `fallback` - classifier failed or returned low confidence; orchestrator
  picked the safest default (dev if an active plan exists, else chat).

### Inspecting a decision in tracer output

```bash
# Just the orchestrator spans, with the mode attribute
jq 'select(.name == "pipeline.orchestrate") | {conv: .conversation_id,
    duration: .duration_s, attrs: .attrs}' alfred_trace.jsonl

# Conversations that got routed to chat (no crew spent)
jq -s 'group_by(.conversation_id)
  | map(select(any(.[]; .name == "pipeline.orchestrate")
               and (any(.[]; .name == "pipeline.run_crew") | not)))
  | map({conv: .[0].conversation_id})' alfred_trace.jsonl
```

### Forcing a specific mode for reproduction

Send a prompt with an explicit `mode` field in the `data` object to
bypass the classifier:

```json
{
  "type": "prompt",
  "msg_id": "uuid",
  "data": {"text": "whatever", "mode": "dev"}
}
```

Valid values: `auto` (default, orchestrator decides), `dev`, `plan`,
`insights`. The processing app accepts these in `websocket.py` around
the prompt-handling branch (~line 495). Use this when a user reports a
mode-routing bug and you want to repro with the exact same mode they got.

### Disabling the orchestrator for a repro

If you suspect the orchestrator is misclassifying a build prompt, set
`ALFRED_ORCHESTRATOR_ENABLED=` (empty) on the processing app and restart.
The `orchestrate` phase becomes a no-op, `ctx.mode` stays at `"dev"`, and
every prompt runs the full Dev pipeline exactly as before Phase A.

### Insights mode debugging (Phase B)

Insights mode short-circuits the pipeline at `_phase_orchestrate` via
`_run_insights_short_circuit`, which runs a single-agent crew with a
read-only MCP tool subset and a hard 5-call budget. Things to check when
an insights turn misbehaves:

**Check the tool budget**: look for this log line near the start of the
insights run:

```
alfred.mcp_tools INFO: init_run_state: budget=5 conversation_id=conv-abc123
```

If the insights reply is "I couldn't gather the site information..." and
the log shows `MCP call budget exceeded (5 >= 5)`, the agent ran out of
tool calls. Either rephrase the user question to be more specific, or
widen the budget in `alfred/handlers/insights.py::_INSIGHTS_TOOL_BUDGET`.

**Verify read-only tool bindings**: Insights mode should never call
`dry_run_changeset`. If you see that in the tracer, something is wrong
with the tool assignment in `alfred/tools/mcp_tools.py`. The insights
subset is defined in `build_mcp_tools(...)["insights"]`.

**Check the message type routing**: An Insights reply comes through as
an `insights_reply` WebSocket message -> `alfred_insights_reply` realtime
event -> an `Alfred Message` row with `message_type="text"` and
`metadata.mode="insights"`. If the UI renders it as a dev-mode reply,
the connection manager's `event_map` in
`alfred_client/api/websocket_client.py` is probably stale.

**Query insights turns in the trace**:

```bash
# Find all Insights-mode turns that spent the full budget
jq 'select(.name == "pipeline.run_crew" and .attrs.mode == "insights"
    and .attrs.calls_made >= 5)' alfred_trace.jsonl
```

**Force Insights for a repro**: set `mode: "insights"` in the prompt
envelope so the orchestrator skips classification entirely and runs the
handler directly:

```json
{"type": "prompt", "msg_id": "uuid",
 "data": {"text": "how many DocTypes in HR?", "mode": "insights"}}
```

### Plan mode debugging (Phase C)

Plan mode short-circuits the pipeline at `_phase_orchestrate` via
`_run_plan_short_circuit`, which runs a 3-agent crew (Requirement,
Assessment, Architect) with a 15-call MCP tool budget and produces a
`PlanDoc`-shaped JSON document. Things to check:

**The plan doc is a stub with "unreadable" or "unavailable" in the
title.** The LLM produced output but it couldn't be parsed as a PlanDoc.
Check the processing-app logs for a warning like:

```
alfred.handlers.plan WARNING: Plan doc validation failed: ...
```

Or:

```
alfred.handlers.plan WARNING: Plan crew output could not be parsed as JSON.
```

The stub doc includes the first ~400 chars of raw agent output under
`open_questions` so you can see what the LLM actually produced. Usual
causes: the Architect wrapped the JSON in code fences (the handler
strips those but only if they're at the top and bottom), the model
hallucinated extra commentary between the JSON and the closing brace,
or the JSON is missing a required field like `title` or `summary`.

**Plan → Dev handoff isn't firing.** After clicking Approve & Build,
the next Dev run should see the plan in its enhanced prompt. Check:

1. Is `ctx.conversation_memory.active_plan` set? Inspect via tracing or
   a temporary log in `_phase_enhance`.
2. Does `_maybe_approve_active_plan()` match the prompt phrasing? The
   pattern list is in `alfred/api/pipeline.py::_PLAN_APPROVAL_PATTERNS`.
   If the user typed something unusual, the plan stays `proposed` and
   `render_for_prompt()` hides the steps.
3. Is the plan status `built` already? Once a Dev run consumes an
   approved plan, it's flipped to `built` and won't re-inject. Inspect
   `memory.active_plan.status`.

**Inspecting plan turns in the trace:**

```bash
# Plan-mode orchestrator decisions
jq 'select(.name == "pipeline.orchestrate")
    | select(.attrs.mode == "plan")' alfred_trace.jsonl
```

**Force Plan for a repro:** send `mode: "plan"` in the prompt envelope.

### Sanitizer behaviour change (Phase A fix)

Pre-Phase-A, any prompt whose intent didn't match a narrow keyword set
returned `allowed=False` from `check_prompt` and surfaced as *"Unable to
classify prompt intent. Flagged for admin review."* Phase A removed that
hard block - unknown intents now pass through with `needs_review=True`
for logging only. The **only** hard gate is now the regex pattern set
(`DEFAULT_INJECTION_PATTERNS`). If you're debugging a report where a
greeting or novel phrasing gets rejected, check:

1. Is the prompt matching an injection pattern by accident?
   Run `sanitize_prompt("the prompt text")` in a Python REPL and inspect
   `result["threats"]` for false positives.
2. Is `check_prompt` actually returning `allowed=False`? A regression
   here would re-surface the original bug. The test that guards it is
   `tests/test_phase6.py::TestCheckPrompt::test_unknown_intent_allowed_with_flag`.

## Reflection drop events

When `ALFRED_REFLECTION_ENABLED=1`, look for these log markers in the
processing app:

```
alfred.reflection INFO: Reflection raw response (first 300): '{"remove": [1, 2], ...}'
alfred.reflection INFO: Reflection dropped 2/3 items: [(1, 'audit log not in request'), (2, 'custom field not asked for')]
alfred.reflection WARNING: Reflection flagged all 3 items; keeping everything. ...
```

The WARNING line is the safety-net: if the reviewer flagged every index in
the changeset, nothing is dropped. This usually means the reviewer misread
the user's request rather than "the whole thing is over-reach" - worth
sampling the prompt if you see it repeatedly for one site.

Dropped items are also emitted to the UI as `minimality_review` WebSocket
events, which the chat UI renders as a purple info banner.

## Conversation Memory Inspection

Memory is stored in Redis under the StateStore namespace:

```bash
redis-cli -p 13000 KEYS "*conv-memory-*"
redis-cli -p 13000 GET "alfred:dev.alfred:task:conv-memory-<conversation-id>"
```

Returned payload:

```json
{
  "conversation_id": "conv-abc",
  "items": [
    {"op": "create", "doctype": "Notification", "name": "Alert on Submit"},
    {"op": "create", "doctype": "Custom Field", "name": "priority", "on": "Sales Order"}
  ],
  "clarifications": [
    {"q": "When should the notification fire?", "a": "On submit"}
  ],
  "recent_prompts": ["Email the approver when a new expense claim is submitted"],
  "updated_at": 1712947200.0
}
```

If a multi-turn conversation is producing weird follow-up responses, check
that memory is populated after the first turn. Empty `items` after a
successful deploy = memory save failed (check for
`alfred.conversation_memory WARNING: save failed` in the log).
