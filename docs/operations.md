# Alfred Operations Runbook

This doc is for operators running Alfred in production: what should be
running, how to restart services safely, what to do when something breaks.
For developer-oriented debugging (log markers, Redis commands, pipeline
tracing), see [debugging.md](debugging.md). For installation and first-time
setup, see [SETUP.md](SETUP.md).

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

# Fix permanently: add to Procfile (see SETUP.md)
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
