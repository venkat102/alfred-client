# Running Alfred in Production

This is the **doc for operators** — installing services, monitoring health, responding to incidents, rotating secrets, debugging stuck conversations.

Sections:

1. **Daily operations** — services, restart procedures, log filters
2. **Observability** — metrics, traces, logs, dashboards
3. **Debugging guide** — common symptoms and where to look
4. **Incident response** — eight scenarios you'll actually see in production
5. **Disaster recovery** — backups, restore, failover

If you're brand new to operating Alfred, read top to bottom (~30 minutes). If you're paged, jump straight to section 4.

---

# Part 1 — Daily Operations


This doc is for operators running Alfred in production: what should be
running, how to restart services safely, what to do when something breaks.
For developer-oriented debugging (log markers, Redis commands, pipeline
tracing), see [running.md](running.md). For installation and first-time
setup, see [getting-started.md](getting-started.md).

---

## Service inventory

A running Alfred deployment has these processes. If any one is missing,
something is broken.

### On the customer's Frappe site

| Process | What it does | How to verify |
|---|---|---|
| `bench start` (or prod supervisor) | Serves the Frappe web app + static assets | `curl http://site:8000/api/method/ping` returns `{"message": "pong"}` |
| `worker_default` | Runs the default RQ queue (short jobs) | `ps aux \| grep 'rq.*default' \| grep -v grep` shows one entry |
| `worker_long` | Runs the `long` queue - this is where `_connection_manager` lives for up to 7200s per active conversation | `ps aux \| grep 'rq.*long' \| grep -v grep` shows one entry. **If missing, Alfred silently queues connection managers forever and nothing happens.** |
| `worker_short` | Runs short-lived jobs | Optional but recommended |
| `frappe_schedule` | Runs scheduled jobs (stale cleanup, etc.) | `ps aux \| grep schedule` |
| Socket.IO node process | Real-time browser updates | `ps aux \| grep socketio` |
| Redis on port 13000 (`redis_cache`) | Frappe cache + Alfred pub/sub + Alfred message queue | `redis-cli -p 13000 ping` returns `PONG` |
| Redis on port 11000 (`redis_queue`) | RQ job queue | `redis-cli -p 11000 ping` returns `PONG` |
| Redis on port 12000 (`redis_socketio`) | Socket.IO state | `redis-cli -p 12000 ping` returns `PONG` |

### On the processing-app host

| Process | What it does | How to verify |
|---|---|---|
| Uvicorn serving `alfred.main:app` | FastAPI WebSocket server | `curl http://host:8001/health` returns `{"status": "ok", "version": "...", "redis": "connected"}` |
| Redis (if running Alfred's own, not reusing Frappe's) | State store + event streams | `redis-cli -p 6379 ping` returns `PONG` |
| Ollama (if local) | LLM provider | `curl http://localhost:11434/api/tags` returns a JSON list of models |

### On the admin portal host (SaaS only)

| Process | What it does | How to verify |
|---|---|---|
| `bench start` | Frappe site serving admin portal UI + APIs | `curl http://admin-host:8000/api/method/ping` |
| `frappe_schedule` | Runs `check_trial_expirations` daily | Check `bench --site admin scheduled-job-list` |

---

## Routine operations

### Restart the processing app

**Native dev (`./dev.sh`):** `Ctrl+C` in the terminal, re-run `./dev.sh`.
Uvicorn reloads on file changes automatically, so you usually don't need
this in development.

**Docker:**

```bash
cd alfred_processing
docker compose restart processing   # restart just the app
docker compose restart               # restart all services (processing + redis + optional ollama)
```

**Production (systemd / supervisor / k8s):** whatever `sudo systemctl
restart alfred-processing` or `kubectl rollout restart deployment/alfred-processing`
looks like in your deploy.

**Expected downtime:** 2-5 seconds of WebSocket disconnection. Active
conversations reconnect automatically with exponential backoff; the user
sees a brief "reconnecting" indicator. Any in-flight pipeline is cancelled
and needs to be retried.

**Do NOT restart during:** a live deploy from an approved changeset.
Restarting during `apply_changeset` can leave the DB in a partial state.
Check `frappe --site X list-jobs` for active Alfred deployment jobs before
restarting.

### Restart the Frappe site

```bash
bench restart                        # restart all workers + gunicorn
bench restart --web                  # just the web server
bench --site dev.alfred restart      # per-site
```

**Side effects:**

- Active WebSocket connections from `_connection_manager` to the processing
  app are killed. The worker respawns after a few seconds and the
  connection manager reconnects via the retry loop.
- Any running `_connection_manager` jobs get marked as failed in RQ.
  `stale_cleanup` will mark stale conversations the next time it runs.
- MySQL connections are closed, which can leave zombie connection-manager
  loops in a `SELECT 1`-failing state. The `_reconnect_db_if_stale()`
  helper detects this on the next DB access and reconnects, so the
  connection manager recovers automatically - but this is also when you
  should verify `worker_long` is alive:
  ```bash
  ps aux | grep 'rq.*long' | grep -v grep
  ```

### Rotate the API secret key

```bash
# 1. Generate a new key
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 2. Update the processing app's .env
vim alfred_processing/.env
#    Change API_SECRET_KEY=<new value>

# 3. Restart the processing app
docker compose restart processing    # or ./dev.sh in native

# 4. Update every customer site's Alfred Settings
#    Open /app/alfred-settings on each site
#    Paste the new value into the API Key field
#    Save

# 5. Restart Frappe workers on each site so active connection managers
#    pick up the new key for their next handshake
bench restart
```

Any conversations that are mid-pipeline during the rotation window will
fail with `Invalid API key` and need to be retried. Plan rotations for a
low-activity window.

### Rotate the admin service key

```bash
# 1. Generate
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# 2. Update the admin portal
#    Open /app/alfred-admin-settings on the admin site
#    Paste into Service API Key, save.

# 3. Update every processing app's .env
vim alfred_processing/.env
#    Change ADMIN_SERVICE_KEY=<new value>

# 4. Restart the processing app
docker compose restart processing
```

No customer-site impact - only the processing app talks to the admin
portal.

### Upgrade Alfred on a customer site

```bash
cd frappe-bench
bench update --pull
# or, per-app:
cd apps/alfred_client && git pull && cd ../..

bench --site your-site migrate      # picks up new DocType fields
bench build --app alfred_client      # rebuilds JS/Vue assets
bench restart                        # reloads workers + web server
```

The `bench migrate` step is where new DocType fields land (e.g.,
`Alfred Plan.pipeline_mode` was added this way). Skipping it leaves the
schema behind the code, so calls that read the new field return `None`
and features silently break.

Things to verify after an upgrade:

- [ ] `/app/alfred-settings` loads without errors.
- [ ] `/app/alfred-chat` loads and the floating status pill flips from
      "Ready" to the live agent name + activity on a test prompt.
- [ ] `worker_long` is still running.
- [ ] Alfred's scheduled job (`stale_conversation_cleanup`) ran at its
      next scheduled tick.

### Upgrade the processing app

```bash
cd alfred_processing
git pull
.venv/bin/pip install -e .           # in case deps changed
./dev.sh                             # native dev; restart with new code
# OR
docker compose build processing
docker compose up -d processing      # prod
```

Processing-app upgrades are zero-downtime-ish: the old container stops,
the new one starts, active WS connections reconnect in a few seconds. No
schema migration on the processing side (Redis is schemaless, just a
key/value store).

---

## Incident response

### "Processing App unreachable" errors flooding the chat UI

**Cause**: WebSocket handshake failing or the processing app is down.

1. `curl http://processing-host:8001/health` - if this fails, the
   processing app is not running. Start it.
2. If health returns OK, the issue is handshake auth. Verify
   `Alfred Settings.api_key` matches `API_SECRET_KEY` in the processing
   app's `.env`.
3. Check the Frappe worker log:
   ```bash
   tail -100 ~/bench/develop/frappe-bench/logs/worker_long.log | grep -i alfred
   ```
   Look for `Invalid API key` or `Handshake failed`.
4. Check the processing app log for incoming handshake attempts. You
   should see `WebSocket connection opened: conversation=...` followed by
   either `authenticated` (success) or `auth failed` (mismatch).

### Chat shows "Processing service is unavailable - contact your admin"

**Cause**: The strict warmup gate in `_phase_warmup` probed one or more
Ollama tier models with a 1-token `/api/generate` call and got back
either a connection error or an HTTP 500 ("model runner has unexpectedly
stopped"). The pipeline emitted `OLLAMA_UNHEALTHY` and exited before the
crew ran, so no LLM tokens were spent on retries.

1. Tail processing app logs for the warmup failure; it names the model
   and base URL:
   ```
   Ollama health probe failed for qwen2.5-coder:14b at http://10.x.x.x:11434: HTTP 500: {"error":"model runner has unexpectedly stopped"}
   ```
2. SSH to the Ollama box and check the service:
   ```bash
   sudo journalctl -u ollama -n 200 | grep -iE "oom|killed|panic|cuda|out of memory"
   nvidia-smi          # (GPU) is another process hogging VRAM?
   ollama ps           # anything still loaded?
   ollama list         # confirm the model isn't half-downloaded
   ```
3. Common root causes and fixes:
   - **GPU OOM** from loading all tier models at once: lower to a single
     tier (clear the per-stage overrides in Alfred Settings) or use a
     smaller coder model (`qwen2.5-coder:7b` fits ~8 GB).
   - **Ollama daemon crashed**: `sudo systemctl restart ollama` and
     re-send the prompt.
   - **Partial `ollama pull`**: `ollama rm <model> && ollama pull <model>`.
4. Check `/metrics` for the counter trend:
   ```
   alfred_llm_errors_total{tier="warmup", error_type="OLLAMA_UNHEALTHY"}
   alfred_llm_errors_total{tier="warmup", error_type="probe_retry"}
   ```
   The first counter rises only when both probe attempts fail (real
   outage or persistent unhealth). The second rises whenever the
   first probe attempt fails but the second-attempt retry succeeds -
   a steady trickle here usually means Ollama is reloading models
   between prompts (harmless, absorbed by the retry + 120s warmup
   cache). A sustained rise in the first counter is the strongest
   leading indicator that the Ollama box needs attention.

### Pipeline hangs at "Step N/6 - ... is working..." for more than 10 minutes

**Cause**: usually LLM unresponsive, sometimes MCP tool call hung.

1. Tail the processing app log: look for the last `MCP request sent` line
   and how long ago it was.
2. If the last line is `crew started` with nothing after, the LLM is not
   responding. Verify with a standalone call:
   ```bash
   cd alfred_processing
   .venv/bin/python test_llm.py
   ```
   If that also hangs, the LLM provider is the issue. Restart Ollama
   (`ollama serve` or `docker restart ollama`) or check your cloud
   provider's status page.
3. If the last line is `Tool X executed successfully` and then nothing,
   the crew is thinking. Wait longer - some prompts take 10-15 minutes on
   local hardware.
4. If the last line is `MCP request sent: tool=... id=...` with no
   response, the client-side MCP dispatch is stuck. Check the Frappe
   worker_long log for errors. A common cause: the connection manager's
   Frappe DB connection went stale after `bench restart`. The
   `_reconnect_db_if_stale()` helper handles this on the next access;
   if it's not recovering, stop and restart the conversation's worker
   entry.

### Nothing happens after clicking Send

**Cause**: `worker_long` is not running, so `_connection_manager` jobs are
queued but never picked up.

```bash
# Verify
ps aux | grep 'rq.*long' | grep -v grep

# Start it (temporary, for quick check)
bench worker --queue long &

# Fix permanently: add to Procfile (see getting-started.md)
echo 'worker_long: OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES NO_PROXY=* bench worker --queue long 1>> logs/worker_long.log 2>> logs/worker_long.error.log' >> Procfile
bench restart
```

### Long queue saturated (queued jobs piling up)

**Symptom**: the chat UI shows an amber banner above the input area
reading "Waiting for a worker - other conversations are holding the
queue"; new chat conversations stall on the first prompt; the Health
dialog shows `Background Job: running` but the Redis queue depth stays
at 1; `redis-cli -p 11000 LLEN 'rq:queue:<site>:long'` is greater than
zero for several seconds.

Each active chat conversation occupies one slot on the `long` worker
queue for up to ~1.75 hours (the connection manager's lifetime cap).
When every slot is held and nothing is draining, new conversations are
forced to wait.

**Fast diagnosis** with the bundled bench command:

```bash
bench --site <site> alfred-reap
```

Dry-runs a list of every running and queued `_connection_manager` with
its conversation ID and age, e.g.:

```
STATE    CONVERSATION       AGE      WORKER/JOB
running  7shilmh6g3         5m 36s   e58192e0272f4e75bf2b63f02bfd8ad4
queued   k0tw9nrb2x         12s      dev.alfred||37a2b8d7-6518-4def-9705-...
```

**Free a slot** by shutting down the managers that are idle (the
conversation has not received a prompt in over an hour):

```bash
bench --site <site> alfred-reap --idle --yes
```

Or target a specific conversation by ID:

```bash
bench --site <site> alfred-reap --conv <conversation-id>
```

Or nuke all of them (prompts for confirmation unless you pass `--yes`):

```bash
bench --site <site> alfred-reap --all
```

Closed conversations reconnect automatically on next open, so reaping
is safe - it only interrupts a manager that was about to time out
anyway.

**Permanent fix**: add more workers to `Procfile` so two idle managers
can never block everyone. Paste beside the existing `worker_long:`:

```
worker_long_2: OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES NO_PROXY=* bench worker --queue long 1>> logs/worker_long_2.log 2>> logs/worker_long_2.error.log
```

Then `bench restart`. With 4 slots a single-user dev box effectively
never hits the jam.

**Nuclear option** - drain the queue entirely:

```bash
# Only if you're sure everything in there is stale
redis-cli -p 11000 DEL 'rq:queue:<site>:long'
bench restart
```

This kills every in-progress conversation. Users will need to retry.

### Alfred stops responding to one specific user

**Cause**: stuck pipeline in their active conversation.

1. Find the conversation:
   ```bash
   # In a bench console:
   bench --site your-site console
   >>> frappe.get_all("Alfred Conversation", filters={"user": "them@example.com", "status": ["in", ["In Progress", "Awaiting Input"]]}, fields=["name", "status", "modified"])
   ```
2. Signal the connection manager to shut down:
   ```python
   >>> frappe.cache().publish("alfred:ws:outbound:<conversation-id>", "__shutdown__")
   ```
3. Mark the conversation stale:
   ```python
   >>> conv = frappe.get_doc("Alfred Conversation", "<id>")
   >>> conv.status = "Stale"
   >>> conv.save(ignore_permissions=True)
   >>> frappe.db.commit()
   ```
4. The user can now start a new conversation or open the stale one to
   reactivate it.

### Deployment failed mid-way with rollback

This is mostly automatic. `apply_changeset` catches exceptions and calls
`_execute_rollback` with the `rollback_data` it's been accumulating.
Symptoms that need manual intervention:

1. **Rollback itself failed** - `frappe.delete_doc` threw an error on one
   of the items. Check `Alfred Changeset.deployment_log` for
   `"status": "failed"` entries. You may need to manually delete the
   stuck document via `/app/desk#List/DocType/<name>` or the Frappe
   console.
2. **Changeset stuck in "Deploying"** - the `apply_changeset` process
   died without reaching the final `frappe.db.commit`. The distributed
   lock (`UPDATE ... SET status='Deploying' WHERE status='Approved'`) is
   still held. Manually reset:
   ```python
   >>> cs = frappe.get_doc("Alfred Changeset", "<name>")
   >>> cs.status = "Failed"    # or "Approved" if you want to retry
   >>> cs.save(ignore_permissions=True)
   >>> frappe.db.commit()
   ```
3. **DDL leaked into the DB** - shouldn't happen with the Phase 3 dry-run
   fix (meta-only path for DocType / Custom Field / Workflow), but if
   you see a stray DocType / Custom Field that wasn't approved, clean it
   up via the Frappe UI and open an issue - we want to know.

### Unexpected changeset deployed without approval

This should be impossible. If it happens:

1. Stop the processing app immediately (potential `enable_auto_deploy`
   misconfiguration or a bug).
2. Check `Alfred Settings.enable_auto_deploy` - if it's on, that's the
   legitimate cause: auto-deploy skips the approval step by design.
3. If auto-deploy is off, pull the Alfred Audit Log for the affected
   site and identify who triggered the deploy:
   ```python
   >>> frappe.get_all("Alfred Audit Log",
   ...     filters={"creation": [">", "2026-04-13 10:00:00"]},
   ...     fields=["conversation", "doctype", "document_name", "operation", "owner"],
   ...     order_by="creation")
   ```
4. Open a security issue - see [SECURITY.md](SECURITY.md#reporting-a-vulnerability)
   for the reporting process.

### Admin portal returns stale plan info

**Cause**: the processing app caches plan checks per-site briefly (~60s).

1. Restart the processing app to clear any in-memory cache.
2. If the issue persists, the admin portal itself may be serving stale
   data - try refreshing `/app/alfred-customer/<site>` in the admin UI
   and check `current_plan` is what you expect.
3. Verify the service key matches between the processing app's
   `ADMIN_SERVICE_KEY` and the admin portal's `Alfred Admin Settings.service_api_key`.

---

## Scheduled maintenance

### Nightly

- `alfred_client.stale_cleanup.mark_stale_conversations` runs daily via
  the Frappe scheduler. Configured in `hooks.py::scheduler_events`.
  Marks conversations with no activity in `stale_conversation_hours`
  (default 24h) as `Stale` so they drop out of the active-conversation
  list.

### Daily

- `alfred_admin.api.billing.check_trial_expirations` runs daily on the
  admin portal. Sends warning emails 3 days before trial end, suspends
  customers whose grace period has elapsed.

### Weekly

- Rotate log files (processing app + Frappe) via logrotate or your
  logging stack.
- Review `alfred_trace.jsonl` if tracing is enabled. Archive or
  truncate if it's grown large; there's no automatic rotation.

### Monthly

- Review Alfred Usage Log on the admin portal for anomalies
  (unexpectedly high / low usage per customer).
- Review Alfred Audit Log on each customer site - quick sanity check
  that deploys are matching what users intended.
- Check for security advisories on the dependencies pinned in
  `alfred_processing/pyproject.toml` and `alfred_client` (search for
  CVEs or run `pip-audit`).

---

## Disaster recovery

### Full processing app loss

Redis is a cache, not source of truth. Losing the processing app's Redis
means:

- **Lost**: in-flight pipeline state (any conversation mid-pipeline fails
  and must be retried), the conversation memory layer, crew state, trace
  JSONL.
- **Not lost**: chat history, changesets, audit logs, rollback data - all
  live in the Frappe site's MySQL.

Recovery: restart the processing app pointed at a fresh Redis. Active
users see one failure, retry, and everything works again.

### Full Frappe site loss

Restore from your normal Frappe backup (you are backing up, right?).
Alfred's data is all in the same MySQL database. `bench backup --with-files`
includes everything.

### Admin portal loss

Customer sites can still run - the processing app's plan-check is
optional, and if the admin portal is unreachable, `check_plan` errors
out and the pipeline falls back to the site's local `pipeline_mode`
setting. Usage reports back up in the processing app's Redis and are
lost when it restarts; rebuild the admin portal from backup and start
fresh usage accounting.

---

## Metrics to monitor (external monitoring)

If you have Grafana / Datadog / New Relic, these are the key series:

| Metric | Where to get it | Alert threshold |
|---|---|---|
| Processing app `/health` latency | HTTP probe | > 1s p95 |
| Processing app `/health` status | HTTP probe | Any non-200 |
| Long queue depth on Frappe | `redis-cli -p 11000 LLEN 'rq:queue:<site>:long'` | > 10 for 5+ min |
| Long worker process count | `pgrep -fc 'rq.*long'` | < 1 (should always be >= 1) |
| MySQL connection count on Frappe | `SHOW PROCESSLIST` | > 80% of max_connections |
| Redis memory (port 13000) | `redis-cli -p 13000 INFO memory` | > 75% of maxmemory |
| Frappe worker log error rate | grep `ERROR` / minute | > 5/min sustained |
| Processing app log error rate | grep `ERROR` / minute | > 5/min sustained |
| Alfred pipeline trace avg duration | `alfred_trace.jsonl` (if enabled) | > 2x baseline |

A simple Prometheus-friendly health endpoint on the processing app
returns `{"status": "ok", "redis": "connected"}` - you can alert on
anything other than that shape. No additional exporter needed.

### Prometheus `/metrics` endpoint

The processing app also exposes a scrape endpoint at `/metrics` in
Prometheus exposition format. Six metrics cover the operational surface:

| Metric | Type | Labels | What it tells you |
|---|---|---|---|
| `alfred_pipeline_phase_duration_seconds` | histogram | `phase` | Per-phase latency. Catches regressions like "warmup suddenly 30s" or "run_crew p95 climbing". Buckets: 0.05, 0.25, 1, 2.5, 5, 10, 30, 60, 180, 600. |
| `alfred_mcp_calls_total` | counter | `tool`, `outcome` | MCP tool invocations sliced by outcome: `success`, `error`, `timeout`, `cached`, `budget_exceeded`. Spot tool-call loops and failure spikes. |
| `alfred_orchestrator_decisions_total` | counter | `source`, `mode` | How the mode was picked: `override` / `fast_path` / `classifier` / `fallback`. Confirms the classifier LLM is actually running in prod vs always falling back. |
| `alfred_llm_errors_total` | counter | `tier`, `error_type` | Standalone LLM errors, sliced by tier and type (`http_error`, `network_error`, `timeout`, `non_json`, ...). Feeds alerting when Ollama wobbles. |
| `alfred_crew_drift_total` | counter | `reason` | Developer agent pivoted out of the task structure (training-data dump, prose-only, foreign doctype). Measures CrewAI quirk tax. |
| `alfred_crew_rescue_total` | counter | `outcome` | Direct-LLM regeneration path ran. `outcome=produced` means it recovered; `empty` means it didn't. Climbing `empty` = rescue is not rescuing. |

Scrape example:

```
scrape_configs:
  - job_name: alfred_processing
    metrics_path: /metrics
    static_configs:
      - targets: ["processing:8001"]
```

Alerting suggestions:

- `rate(alfred_llm_errors_total{error_type="timeout"}[5m]) > 0.5` → Ollama wedged
- `rate(alfred_crew_rescue_total{outcome="empty"}[15m]) > rate(alfred_crew_rescue_total[15m]) * 0.5` → rescue is failing half the time; the agent prompts or the model have regressed
- `histogram_quantile(0.95, rate(alfred_pipeline_phase_duration_seconds_bucket{phase="run_crew"}[5m])) > 600` → crew p95 > 10 min, something is stuck

Quick one-shot scrape:

```bash
curl -s http://localhost:8001/metrics | grep alfred_
```

---

## Common log filters

### Processing app - happy path for one conversation

```bash
grep "conversation=<id>" logs/processing.log | head -50
```

Look for the phase progression:

```
WebSocket connection opened: conversation=<id>
WebSocket authenticated: user=... site=...
Pipeline mode resolved for <site>: full (source=site_config)
Built 6 agents with LLM: ollama/qwen2.5-coder:32b
(agent events...)
Pre-preview dry-run via MCP
(changeset delivered)
```

### Frappe worker - connection manager happy path

```bash
grep -E "alfred.ws_client|alfred.mcp.server" ~/bench/develop/frappe-bench/logs/worker_long.log | head -30
```

### Errors only

```bash
# Processing app
grep -E "ERROR|CRITICAL" logs/processing.log | tail -30

# Frappe worker
grep -E "ERROR|CRITICAL" ~/bench/develop/frappe-bench/logs/worker*.log | tail -30
```

### Security-relevant events

```bash
# Failed auth handshakes
grep -E "Invalid API key|JWT error|Handshake failed" logs/processing.log

# Permission denials in the deploy path
grep "PermissionError" ~/bench/develop/frappe-bench/logs/worker*.log

# Rejected prompts
grep "PROMPT_BLOCKED" logs/processing.log
```

---

# Part 2 — Observability

Logs, metrics, tracing, event streams. Cross-process — covers both the Frappe site (alfred_client) and the Processing App (alfred_processing).


Consolidated reference for how alfred_processing exposes operational signals.
Five independent surfaces, each answers a different question:

| Surface | Answer to | Where |
|---|---|---|
| Structured logs | "what's happening right now, line by line" | stdout (Docker picks it up), log-level configurable |
| Prometheus metrics | "how is the fleet doing, aggregated, over time" | `GET /metrics` |
| Span tracer | "how long did each pipeline phase take, with context" | JSONL file + optional stderr |
| Event stream (Redis) | "replay what this conversation actually emitted" | `alfred:{site_id}:events:{conversation_id}` stream |
| Admin portal usage reports | "how many tokens / conversations are billed to which site" | HTTP POST to admin portal |

Read this doc once; future changes to an observability surface should update it.

## 1. Logs

### Setup (`alfred/main.py:13-22`)

```
level=DEBUG, stream=stdout, format='%(asctime)s %(name)s %(levelname)s: %(message)s'
```

Root level is `DEBUG` but per-logger overrides trim production noise:
- `alfred.*` stays at DEBUG (application logs)
- `websockets` / `httpcore` / `LiteLLM` drop to WARNING (library chatter)

### Module loggers

Every module uses `logger = logging.getLogger("alfred.<area>")`. Common names:

| Logger | Used for |
|---|---|
| `alfred.auth` | REST + WS auth paths |
| `alfred.crew` | CrewAI dispatch, specialist selection |
| `alfred.defense` | Prompt sanitizer verdicts |
| `alfred.llm_client` | Ollama HTTP calls, timeouts, retries |
| `alfred.pipeline` | Pipeline phase lifecycle |
| `alfred.ratelimit` | Rate-limit hits |
| `alfred.state` | Redis reads/writes |
| `alfred.tracer` | Tracer own-diagnostics |

### PII + secret discipline

INFO lines carry metadata only (user email, site_id, msg_id, lengths, status codes). User-content-derived strings (prompts, clarifier answers, LLM raw output) are logged at DEBUG only.

Call sites that followed this split after 2026-04-24:

- `alfred/tools/user_interaction.py:66, 73` — clarifier question send + user-response receive
- `alfred/agents/reflection.py:204` — reflection LLM output
- `alfred/api/websocket.py:1041` — clarify LLM output

Any new logger call touching user content must follow the same pattern: INFO = lengths + ids, DEBUG = text.

Secrets are never logged. `API_SECRET_KEY` + `llm_api_key` only appear in settings objects that are never pretty-printed.

## 2. Prometheus metrics (`alfred/obs/metrics.py`)

Scrape at `GET /metrics` (mounted in `alfred/main.py:130` via `make_asgi_app`). No auth on this endpoint; firewall externally.

### Metrics shipped

| Metric | Type | Labels | What it answers |
|---|---|---|---|
| `alfred_pipeline_phase_duration_seconds` | Histogram | `phase` | "Which pipeline phase regressed when p99 blew up?" |
| `alfred_mcp_calls_total` | Counter | `tool`, `outcome` | "Is an agent stuck in a tool-call loop?" |
| `alfred_orchestrator_decisions_total` | Counter | `mode`, `source`, `confidence` | "Is the mode classifier LLM actually running, or is the fallback eating everything?" |
| `alfred_llm_errors_total` | Counter | `tier`, `error_type` | "Is Ollama down?" |

### What's deliberately NOT a metric

- LLM success throughput → the tracer's span duration already captures it
- Per-conversation event counts → the Redis event stream is the source of truth
- Individual user actions → privacy concern; aggregate via tracing infra if needed

### Adding a new metric

Register it in `alfred/obs/metrics.py` using the default registry (`DEFAULT_REGISTRY` from prometheus_client). `make_asgi_app()` picks it up automatically.

## 3. Span tracer (`alfred/obs/tracer.py`)

Zero-dep async-safe span tracer. Call-site API matches OpenTelemetry's context manager so switching to a real OTel SDK later is mechanical.

### Enabling

```sh
ALFRED_TRACING_ENABLED=1
ALFRED_TRACE_PATH=./alfred_trace.jsonl     # default
ALFRED_TRACE_STDOUT=1                       # optional stderr summary
```

Default OFF. Enable in production selectively if you need phase-level timing.

### What traces contain

Each span is a JSONL object: `{name, attrs, events, status, duration_s, start, end, trace_id, span_id, parent_span_id, error?}`.

Spans carry metadata only (phase name, module, intent, conversation_id, duration, status). Spans do NOT carry user prompts, replies, or LLM output. Verified: no `tracer.span(...)` caller passes user content as attrs.

### Path safety (`ALFRED_TRACE_PATH` whitelist)

`_safe_trace_path()` rejects inputs that would write outside a permitted-root whitelist (CWD, `$HOME`, `tempfile.gettempdir()`, `/tmp`, `/var/tmp`). Inputs with `..` components or targets outside the whitelist fall back to the default with a WARNING.

The cap defends against an attacker who can set process env vars (container env injection, CI secret leakage) from redirecting trace writes to `/etc/systemd/system/…override` or similar. Tests: `tests/test_tracer_path_validation.py`.

### Exporters

- `jsonl_file_exporter(path)` — append one JSON object per line
- `stdout_exporter(span)` — human-readable summary to stderr
- Register additional exporters via `tracer.register_exporter(callable)`; each gets the finished span dict

## 4. Event stream (Redis — `alfred/state/store.py`)

Every user-visible WebSocket message is mirrored into a Redis stream keyed `alfred:{site_id}:events:{conversation_id}`. Used by the `resume` WS handler to replay events after a client reconnect (see `developing.md` for the WS contract).

### What's persisted

`ConnectionState.send()` appends to the stream automatically. Persisted: `agent_status`, `agent_activity`, `changeset`, `chat_reply`, `insights_reply`, `plan_doc`, `info`, `error`, `minimality_review`, `clarify`, `validation`, `question`, `run_cancelled`, `mode_switch`, `auth_success`, others by omission.

Skipped (transport/meta): `ack`, `ping`, `mcp_response`, `echo` — see `_STREAM_SKIP_TYPES`.

### Retention

- `maxlen` = 10,000 events per conversation (oldest trimmed on push)
- Key-level TTL = 7 days; refreshed on every `push_event`. Active conversations stay alive indefinitely; a silent conversation auto-reaps.
- TTL is configurable via `StateStore(stream_ttl_seconds=N)`; `0` disables auto-expiry.

### Read paths

- WS `resume` handler (`alfred/api/websocket.py` `_handle_custom_message`)
- REST `/api/v1/tasks/{task_id}/messages?since_id=…` (returns events since a stream ID)

### PII posture

Events contain the full WS payload, so they include user-visible content (agent replies, changeset contents, clarifier questions). The stream is tenant-scoped (`site_id` is part of the key) and only readable by the session owner's code path. Redis itself should be behind auth and not exposed externally — that is the trust boundary; the stream does not add additional protection.

## 5. Admin portal usage reports (`alfred/api/admin_client.py`)

SaaS-only: when `ADMIN_PORTAL_URL` + `ADMIN_SERVICE_KEY` are configured, the pipeline calls the admin portal for plan checks + usage reports.

Self-hosted: omit those env vars and this path is skipped entirely (`alfred/api/pipeline.py:944` short-circuits).

### check_plan

Invoked in `_phase_plan_check`. Returns `{allowed, tier, warning?, reason?, pipeline_mode?}`. Cached in Redis (`alfred:{site_id}:plan_cache`) for a short TTL so hot paths don't call out on every prompt.

**Policy note — fail-open on outage:** if the admin portal is unreachable and there's no cached verdict, `check_plan` returns `{allowed: True, tier: "offline", reason: "Admin Portal unreachable"}`. Intentional trade-off: customer UX > perfect billing accuracy during an outage. Out-of-band reconciliation (quota audits) is expected to catch overages after the portal recovers. Flip to fail-closed by editing `admin_client.py:65-66` if your deployment prefers reject-during-outage.

### report_usage

Fire-and-forget. Failures are queued in Redis and flushed when the portal recovers.

## Env-var index

All observability-adjacent env vars in one table:

| Var | Default | Effect |
|---|---|---|
| `ALFRED_TRACING_ENABLED` | off | Enable span tracer |
| `ALFRED_TRACE_PATH` | `./alfred_trace.jsonl` | JSONL output (whitelisted roots) |
| `ALFRED_TRACE_STDOUT` | off | Also emit stderr summary |
| `CREWAI_DISABLE_TELEMETRY` | `true` (set by `main.py:16-19`) | Opt out of CrewAI phone-home |
| `CREWAI_DISABLE_TRACKING` | `true` (set by `main.py`) | Opt out of CrewAI tracking |
| `OTEL_SDK_DISABLED` | `true` (set by `main.py`) | Skip OTel SDK cold-start |
| `ADMIN_PORTAL_URL` | empty | Disables admin portal integration when empty |
| `ADMIN_SERVICE_KEY` | empty | Same |

## Troubleshooting

**Traces aren't appearing.** Check `ALFRED_TRACING_ENABLED=1`. Look for the "Rejecting ALFRED_TRACE_PATH" warning in logs — the whitelist may have rejected your configured path.

**`/metrics` returns 404.** `prometheus_client` isn't installed or `app.mount("/metrics", ...)` failed at startup. Check startup logs for import errors.

**CrewAI telemetry is still hitting the network.** Confirm env vars are set BEFORE any `from crewai import …`. `alfred/main.py` sets them at module-import time via `os.environ.setdefault`; if you start the app with `python -c "import alfred.something_else"` you bypass that.

**Resume replay sends nothing on reconnect.** Three common causes: (1) client didn't include `last_msg_id` in the `resume` payload, (2) Redis is unreachable so the stream is empty, (3) the client's `last_msg_id` was trimmed out of the 10k/7d window — server replays the full remaining window, client should dedupe by `msg_id`.

**Admin portal plan check is "always allowed".** Either (a) `ADMIN_PORTAL_URL` is empty (self-hosted mode) and the plan-check phase is skipped, (b) the portal is reachable and actually returns `allowed: True`, or (c) the portal is unreachable and fail-open kicked in (log: "Admin Portal unreachable for plan check"). Cross-check in logs.

---

# Part 3 — Debugging Guide

Common pitfalls + where-to-look table. Cross-references running.md scenarios.


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

See `running.md` for the six Prometheus metrics and alerting
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
