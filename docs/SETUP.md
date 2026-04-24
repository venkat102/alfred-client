# Alfred - Complete Setup & Usage Guide

Everything you need to go from zero to a working Alfred installation. Follow these steps in order.

> **New here?** This is the right document. Read this first to set up Alfred, then see the [User Guide](user-guide.md) for detailed usage instructions.
>
> **Just want to try it fast?** Jump to [Quick Start](#quick-start) below.

---

## Quick Start (5 minutes, assumes Frappe bench + Docker already installed)

```bash
# 1. Install the client app on your Frappe site
cd frappe-bench
bench get-app https://github.com/your-org/alfred_client.git
bench --site your-site install-app alfred_client
bench --site your-site migrate
bench build --app alfred_client

# 2. Start the processing app
cd /path/to/alfred_processing
cp .env.example .env
# Edit .env: set API_SECRET_KEY (generate with: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
docker compose --profile local-llm up -d
docker exec -it $(docker ps -qf "ancestor=ollama/ollama") ollama pull llama3.1

# 3. Configure: open /app/alfred-settings in your browser
#    - Connection tab: set Processing App URL = ws://localhost:8001, paste your API_SECRET_KEY
#    - LLM tab: set Provider = ollama, Model = ollama/llama3.1, Base URL = http://localhost:11434
#    - Save

# 4. Open /app/alfred-chat and start chatting!
```

If any step fails, read the detailed sections below.

---

## Table of Contents

1. [What is Alfred?](#what-is-alfred)
2. [System Requirements](#system-requirements)
3. [Architecture Overview](#architecture-overview)
4. [Part A: Set Up the Frappe Site](#part-a-set-up-the-frappe-site)
5. [Part B: Set Up the Processing App](#part-b-set-up-the-processing-app)
6. [Part C: Connect Them Together](#part-c-connect-them-together)
7. [Part D: Verify the Installation](#part-d-verify-the-installation)
8. [Part E: Using Alfred](#part-e-using-alfred)
9. [Part F: Admin Portal (Optional)](#part-f-admin-portal-optional)
10. [Configuration Reference](#configuration-reference)
11. [Troubleshooting](#troubleshooting)
12. [LLM Provider Configuration](#llm-provider-configuration) (Ollama local/remote, Claude, GPT, Gemini, Bedrock)
13. [Production Deployment](#production-deployment)
14. [Updating Alfred](#updating-alfred)
15. [Backup & Recovery](#backup--recovery)
16. [Monitoring](#monitoring)

---

## What is Alfred?

Alfred is an AI assistant that builds Frappe/ERPNext customizations through conversation. You tell it what you need in plain English - a new DocType, a workflow, a server script - and it designs, generates, validates, and deploys the solution to your Frappe site.

**Alfred consists of 3 components:**
- **Client App** (`alfred_client`) - A Frappe app installed on your site. Provides the chat UI, runs MCP tools for site context, and executes deployments.
- **Processing App** (`alfred_processing`) - A standalone FastAPI service that runs AI agents. Communicates with the client app via WebSocket.
- **Admin Portal** (`alfred_admin`) - Optional Frappe app for managing customers, plans, and billing (only needed if you're offering Alfred as a service).

---

## System Requirements

### For Development / Single Site

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| RAM | 8 GB | 16 GB |
| CPU | 4 cores | 8 cores |
| Storage | 10 GB free | 20 GB free |
| Python | 3.10+ | 3.11 |
| Node.js | 18+ | 20+ |
| Docker | 20+ (production only) | Latest |

### Software Prerequisites

- Frappe Bench (v15+) installed and working (`bench start` runs)
- A Frappe site created (e.g., `dev.alfred`)
- Redis running (Frappe's bench includes this)
- Docker + Docker Compose (for production deployment; not needed for local development)

---

## Architecture Overview

```
Your Browser
    │
    │ Socket.IO (real-time updates)
    ▼
┌──────────────────────────────────┐
│  Your Frappe Site                │
│  (alfred_client app installed)   │
│                                  │
│  /app/alfred-chat ← Chat UI      │
│  /app/alfred-settings ← Config   │
│  MCP Server (12 tools, Framework │
│     KG + pattern library)        │
│  Deployment Engine (savepoint +  │
│     meta-only DDL path)          │
└──────────┬───────────────────────┘
           │ WebSocket (outbound)
           ▼
┌──────────────────────────────────┐
│  Processing App                  │
│  FastAPI + CrewAI agents         │
│  AgentPipeline state machine     │
│  (12 phases, tracer spans)       │
│  (native dev / Docker prod)      │
│  ┌────────┐  ┌────────┐          │
│  │ Redis  │  │  LLM   │          │
│  └────────┘  └────────┘          │
└──────────────────────────────────┘
```

---

## Part A: Set Up the Frappe Site

If you already have a Frappe bench with a site, skip to step 3.

### 1. Install Frappe Bench (if not already installed)

```bash
# Follow the official Frappe installation guide for your OS:
# https://frappeframework.com/docs/user/en/installation
```

### 2. Create a Site (if not already created)

```bash
bench new-site dev.alfred
bench use dev.alfred
```

### 3. Install the Alfred Client App

```bash
# From your bench directory:
cd frappe-bench

# Option A: Install from git repository
bench get-app https://github.com/your-org/alfred_client.git
bench --site dev.alfred install-app alfred_client

# Option B: If the app code is already in your apps/ directory
bench --site dev.alfred install-app alfred_client
```

> **Where to get the code**: If you received Alfred as a zip file or private repo, place the `alfred_client` folder inside `frappe-bench/apps/` and use Option B. If it's a git repository, use Option A with the URL your team provided.

### 4. Run Database Migration

```bash
bench --site dev.alfred migrate
```

### 5. Build Frontend Assets

```bash
bench build --app alfred_client
```

### 6. Verify Installation

```bash
# Check the app is installed
bench --site dev.alfred list-apps
# Should show: alfred_client

# Start the site
bench start
```

Open `http://dev.alfred:8000/app/alfred-settings` in your browser. You should see the Alfred Settings page with tabs for Connection, LLM Configuration, Access Control, Limits, and Usage.

---

## Part B: Set Up the Processing App

The processing app is a Python FastAPI service. **You have two ways to run it:**

| Mode | Use when | What runs | Command |
|---|---|---|---|
| **Native (dev)** | Local development on your laptop | Python venv on the host, reuses Frappe's Redis | `./dev.sh` |
| **Docker (prod)** | Production deployment | Docker container + dedicated Redis container | `docker compose up -d` |

For development, **native mode is strongly recommended**: it's faster to iterate (auto-reload on file save), avoids Docker daemon dependency, and doesn't run a redundant Redis (it reuses the one already running as part of `bench start`).

### 1. Get and Navigate to the Processing App

```bash
# Clone the processing app repository
git clone https://github.com/your-org/alfred_processing.git
cd alfred_processing
```

> **Where to get the code**: Your team will provide the repository URL or a zip file. The processing app is a standalone Python project - it does NOT go inside the Frappe bench. Place it anywhere on your server (e.g., `/opt/alfred_processing` or `~/alfred_processing`).

### 2. Create Your Environment File

```bash
cp .env.example .env
```

Edit `.env` with your settings:
```env
# REQUIRED: Generate a strong random key (used for JWT signing + API auth)
# Use: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
API_SECRET_KEY=your-generated-secret-key-here

# Redis (default works with Docker Compose)
REDIS_URL=redis://redis:6379/0

# LLM Configuration (Ollama = free, local, no API key needed)
# Default is a coder-tuned model - the Developer agent drifts into
# prose with generic chat models. See admin-guide.md > "Recommended
# Ollama models" for tier picks and VRAM sizing.
FALLBACK_LLM_MODEL=ollama/qwen2.5-coder:7b
FALLBACK_LLM_BASE_URL=http://ollama:11434

# CORS - set to your Frappe site URL in production
# ALLOWED_ORIGINS=https://your-site.frappe.cloud
ALLOWED_ORIGINS=*

# Server
HOST=0.0.0.0
PORT=8001
WORKERS=2
DEBUG=false

# CrewAI outbound telemetry - OFF by default. CrewAI ships a built-in
# exporter that POSTs agent run metadata to its own SaaS. These three
# flags disable it (all three set to cover older + newer CrewAI
# versions, and they also short-circuit the embedded OTel SDK init,
# trimming cold-start). Leave them on unless you specifically want to
# send agent run metadata off-site.
#
# NOTE: alfred.main also sets these three flags via os.environ.setdefault()
# at import time as a belt-and-braces default, so forgetting them in .env
# no longer silently phones telemetry home. To opt BACK IN for debugging,
# set them here to "false" - setdefault respects an explicit value.
CREWAI_DISABLE_TELEMETRY=true
CREWAI_DISABLE_TRACKING=true
OTEL_SDK_DISABLED=true
```

> **Port conflict with Frappe**: Frappe's bench runs on port **8000** by default. Since the processing app also defaults to 8000, you **must** change one of them when both run on the same machine. We recommend setting the processing app to `PORT=8001` in `.env`. All examples in this guide use port 8001. Adjust if you chose a different port.

> **Native dev REDIS_URL**: For native mode, set `REDIS_URL=redis://localhost:13000/0` in `.env` so the processing app reuses Frappe's cache Redis (port 13000). Do **not** use port 11000 (that's Frappe's RQ/queue Redis - a different instance). The `docker-compose.yml` ignores this setting and hardcodes its own value, so the same `.env` file works for both modes.

### 3a. Start the Services - Native (Development)

**Prerequisites**: Python 3.11 (`brew install python@3.11` on macOS), Frappe bench already running (`bench start` provides the Redis on port 11000).

```bash
cd alfred_processing
./dev.sh
```

`dev.sh` will:
1. Create a Python 3.11 venv in `.venv/` if missing (or recreate if stale)
2. Install/update deps from `pyproject.toml`
3. Load `.env` and validate `API_SECRET_KEY` is set
4. Kill any stale process holding the port
5. Start uvicorn with `--reload` (auto-reloads on changes to `alfred/**/*.py`)

The service comes up at `http://localhost:8001`. Press `Ctrl+C` to stop. Logs stream to your terminal.

> **Why Python 3.11 and not your system Python?** `crewai` pulls in `chromadb`, `onnxruntime`, `tiktoken`, and `pydantic-core` - these have prebuilt wheels for 3.11 but often lag on newer CPython releases. Sticking to 3.11 = predictable installs, no compilation surprises.

> **Don't have python3.11?** Override with `PYTHON_BIN=python3.12 ./dev.sh` (3.12 also has wheel coverage for crewai's deps; 3.13/3.14 are riskier).

### 3b. Start the Services - Docker (Production)

Choose the command that matches your Ollama setup:

```bash
# Remote Ollama (Ollama runs on another server - configure URL in .env)
docker compose up -d

# Local Ollama on CPU (starts Ollama container alongside processing + redis)
docker compose --profile local-llm up -d

# Local Ollama with GPU (requires NVIDIA Container Toolkit)
docker compose --profile local-llm --profile gpu up -d
```

The default (`docker compose up -d`) starts **2 containers**: Processing App (port 8001) + Redis (port 6379).
Adding `--profile local-llm` adds a **3rd container**: Ollama (port 11434).

#### Stopping and restarting

```bash
# Stop containers (keeps data, can restart quickly)
docker compose stop

# Restart stopped containers
docker compose start

# Stop and remove containers (restart with 'docker compose up -d')
docker compose down

# Stop, remove containers, AND delete all data (Redis state lost)
docker compose down -v

# View running containers and their status
docker compose ps

# Rebuild after code changes (pulls new image, restarts)
docker compose up -d --build
```

### 4. Pull the LLM Model (local Ollama only)

Skip this step if using remote Ollama - the model is already on the remote server.

This downloads the AI model (~4.5 GB for `qwen2.5-coder:7b`, Q4_K_M).
Only needed once.

```bash
docker exec -it $(docker ps -qf "ancestor=ollama/ollama") ollama pull qwen2.5-coder:7b
```

**For more horsepower**, pull the larger coder and set tier overrides
in Alfred Settings (see `admin-guide.md` > "Recommended Ollama models"):

```bash
docker exec -it $(docker ps -qf "ancestor=ollama/ollama") ollama pull qwen2.5-coder:14b
docker exec -it $(docker ps -qf "ancestor=ollama/ollama") ollama pull qwen2.5:7b     # Triage + Reasoning
```

**For machines with less RAM** (< 8 GB), use a smaller coder:
```bash
docker exec -it $(docker ps -qf "ancestor=ollama/ollama") ollama pull qwen2.5-coder:3b
```
Then set `FALLBACK_LLM_MODEL=ollama/qwen2.5-coder:3b` in `.env` and restart.
Expect more rescue events at this size (watch `alfred_crew_drift_total`
on `/metrics`).

### 5. Verify the Processing App

```bash
curl http://localhost:8001/health
```

Expected response:
```json
{"status": "ok", "version": "0.1.0", "redis": "connected"}
```

Also verify the API docs load: open `http://localhost:8001/docs` in your browser.

---

## Part C: Connect Them Together

### 1. Configure Alfred Settings

Open your Frappe site and navigate to: **`/app/alfred-settings`**

Fill in these fields:

#### Connection Tab
| Field | Value | Notes |
|-------|-------|-------|
| Processing App URL | `ws://localhost:8001` | Use your server IP if Frappe and Docker are on different machines |
| API Key | *(paste the `API_SECRET_KEY` from your `.env`)* | Must match exactly |
| Self-Hosted Mode | ✓ Check this | You're running your own processing app |

#### LLM Configuration Tab
| Field | Value | Notes |
|-------|-------|-------|
| LLM Provider | `ollama` | Works for both local and remote Ollama |
| LLM Model | `qwen2.5-coder:7b` (or `:14b` / `:32b` if you have the VRAM) | Must be a coder-tuned model. Auto-prefixed to `ollama/qwen2.5-coder:7b` on save. See [admin-guide.md > Recommended Ollama models](admin-guide.md#recommended-ollama-models) for tier-based setup. |
| LLM API Key | *(leave empty)* | Only needed if your Ollama endpoint requires auth |
| LLM Base URL | `http://localhost:11434` | For remote Ollama: `http://your-server-ip:11434` |
| Max Tokens | `4096` | |
| Temperature | `0.1` | |

> **Using a remote Ollama?** Set the Base URL to your server's address (e.g., `http://135.13.20.57:11434`). Alfred connects to it the same way as local - just a different URL. See [Ollama Configuration](#ollama-local-and-remote) below for details.

#### Access Control Tab
| Field | Value |
|-------|-------|
| Allowed Roles | Add "System Manager" (or any roles that should use Alfred) |

#### Limits Tab
Leave defaults or adjust:
| Field | Default | Description |
|-------|---------|-------------|
| Max Retries Per Agent | 3 | How many times an agent retries before escalating |
| Max Tasks Per User Per Hour | 20 | Rate limit |
| Task Timeout | 300 seconds | Max time per agent phase |
| Stale Conversation Hours | 24 | Mark inactive conversations stale |

**Save the settings.**

### 2. Add a Long Queue Worker (Required)

Alfred's WebSocket connection manager runs as a long-lived background job (up to 2 hours per conversation). Frappe's default Procfile only has a `default` queue worker - you **must** add a `long` queue worker, or connection manager jobs will queue up and never execute.

Add this line to your `Procfile` (in the bench root):

```
worker_long: OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES NO_PROXY=* bench worker --queue long 1>> logs/worker_long.log 2>> logs/worker_long.error.log
```

> **Why is this needed?** `start_conversation()` enqueues `_connection_manager` with `queue="long"` and `timeout=7200`. Without a worker listening on the `long` queue, these jobs pile up indefinitely and no messages reach the Processing App.

### 3. Restart Frappe Workers

```bash
bench restart
```

---

## Part D: Verify the Installation

### UI Health Checks (Recommended)

Open **`/app/alfred-settings`** and use the buttons under the **Actions** dropdown:

| Button | What it checks |
|--------|---------------|
| **Test Processing App** | Hits the Processing App's `/health` endpoint. Shows version and Redis status. |
| **Test LLM Connection** | For Ollama: checks reachability, lists installed models, sends a test generation. For cloud providers: sends a minimal completion call via LiteLLM. |

Fix any red/orange results before proceeding.

### CLI Health Checks

```bash
# 1. Processing app is running
curl http://localhost:8001/health
# Expected: {"status": "ok", "redis": "connected"}

# 2. Ollama has a model (if using Ollama)
curl http://localhost:11434/api/tags
# Expected: list containing "llama3.1" or your chosen model

# 3. Frappe site is running
curl http://dev.alfred:8000/api/method/frappe.client.get_count?doctype=Alfred+Settings
# Expected: {"message": 1}

# 4. Alfred page is accessible
# Open http://dev.alfred:8000/app/alfred-chat in your browser
```

### First Conversation Test

1. Open `http://your-site:8000/app/alfred-chat`
2. You should see the Alfred welcome screen with example prompts
3. Click **"Create a DocType called Book with title, author, and ISBN fields"**
4. Watch the pipeline progress:
   - The floating status pill at the top flips from "Ready" to a
     pulsing gradient mark with the live agent name and activity
     ("Developer - generating code"). Click it to expand the
     six-step pipeline popover.
   - Typing indicator (bouncing dots) appears while agents work.
5. When the changeset preview arrives, the right-edge preview drawer
   slides in automatically. Review:
   - Field table showing the DocType fields
   - Permission matrix
   - (You can minimize the drawer to a chip at the bottom-right and
     reopen it any time; the toolbar also carries a preview toggle.)
6. Click **"Approve & Deploy"** inside the drawer
7. Confirm in the dialog
8. Verify: navigate to `/app/book` - you should see the new DocType

---

## Part E: Using Alfred

Setup is complete. For the full usage guide - including step-by-step conversation walkthrough, what each screen element means, how to handle errors, escalation, rollback, and tips for writing better prompts - see the **[User Guide](user-guide.md)**.

### Quick Reference

| Action | How |
|--------|-----|
| Open Alfred | Navigate to `/app/alfred-chat` |
| Start a conversation | Click "Start a Conversation" or an example prompt |
| Send a message | Type in the input box, press Enter |
| Answer a question | Click an option button or type your answer |
| Approve changes | Click "Approve & Deploy" in the preview drawer |
| Request modifications | Click "Request Changes", then describe what to change |
| Reject changes | Click "Reject" |
| Find past conversations | They're listed on the main Alfred page with summaries |
| Check audit trail | Go to `/app/alfred-audit-log` |
| Rollback a deployment | Go to the Alfred Changeset record, click Rollback |

---

## Part F: Admin Portal (Optional)

Only needed if you're running Alfred as a SaaS product for multiple customers.

### Install

```bash
bench get-app /path/to/alfred_admin
bench --site your-admin-site install-app alfred_admin
bench --site your-admin-site migrate
```

### Configure

1. Go to `/app/alfred-admin-settings`
2. Set **Service API Key** - this authenticates the processing app
3. Set **Default Plan** - auto-assigned to new customers
4. Create plans at `/app/alfred-plan`

### Connect Processing App to Admin Portal

Add to your processing app `.env`:
```env
ADMIN_PORTAL_URL=https://your-admin-site.frappe.cloud
ADMIN_SERVICE_KEY=the-service-api-key-from-admin-settings
```

---

## Configuration Reference

### Alfred Settings (Customer Site)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| processing_app_url | Data | - | WebSocket URL of the processing app. Must be `https://` or `wss://` unless pointing at loopback (`localhost`, `127.x`, `::1`). Save is rejected with an actionable error when the host is public/private-network over plaintext, because the handshake carries `llm_api_key` in `site_config`. |
| api_key | Password | - | Shared secret for authentication |
| self_hosted_mode | Check | No | Enable for self-hosted deployments |
| redis_url | Data | - | Redis URL (self-hosted only) |
| llm_provider | Select | - | ollama, anthropic, openai, gemini, bedrock |
| llm_model | Data | - | Model ID (e.g., codegemma:7b, llama3.1). Auto-prefixed with provider on save. |
| llm_api_key | Password | - | API key for cloud LLM providers |
| llm_base_url | Data | - | Custom endpoint URL |
| llm_max_tokens | Int | 4096 | Max tokens per response |
| llm_temperature | Float | 0.1 | Generation randomness (0.0-2.0) |
| allowed_roles | Table | - | Roles permitted to use Alfred |
| enable_auto_deploy | Check | No | Skip manual approval |
| max_retries_per_agent | Int | 3 | Retries before escalation |
| max_tasks_per_user_per_hour | Int | 20 | Rate limit per user |
| task_timeout_seconds | Int | 300 | Per-agent timeout |
| stale_conversation_hours | Int | 24 | Hours before marking stale |

### Processing App Environment Variables

**Core**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| API_SECRET_KEY | Yes | - | JWT signing key + API auth key |
| REDIS_URL | Yes | redis://redis:6379/0 | Redis connection URL |
| HOST | No | 0.0.0.0 | Bind address |
| PORT | No | 8000 | Server port |
| WORKERS | No | 4 | Uvicorn worker count |
| FALLBACK_LLM_MODEL | No | - | Default LLM model |
| FALLBACK_LLM_API_KEY | No | - | Default LLM API key |
| FALLBACK_LLM_BASE_URL | No | - | Default LLM endpoint |
| LLM_TIMEOUT | No | 120 | LLM request timeout in seconds (per call, not per pipeline) |
| ALLOWED_ORIGINS | No | * | CORS origins (comma-separated) |
| ADMIN_PORTAL_URL | No | - | Admin portal URL (SaaS mode) |
| ADMIN_SERVICE_KEY | No | - | Admin portal auth key |
| DEBUG | No | false | Enable debug logging |

**Feature flags** (all optional, all default off)

| Variable | Default | Description |
|----------|---------|-------------|
| ALFRED_ORCHESTRATOR_ENABLED | off | Enable the three-mode chat orchestrator. When on, prompts are classified into `dev` / `plan` / `insights` / `chat` and conversational / read-only turns short-circuit the crew. See the three-mode chat section of `how-alfred-works.md` for the full flow. `1` to enable. |
| ALFRED_REFLECTION_ENABLED | off | Enable the post-crew reflection step that drops items the user didn't ask for. Accepts `1` / `true` / `yes`. Default off for cautious rollout. Once on, the UI shows `minimality_review` events when the reviewer trims an over-reaching changeset. |
| ALFRED_TRACING_ENABLED | off | Enable structured span tracing. Emits one JSON object per pipeline phase to `ALFRED_TRACE_PATH`. Accepts `1` / `true` / `yes`. |
| ALFRED_TRACE_PATH | `./alfred_trace.jsonl` | JSONL output file for tracer spans. Only relevant when `ALFRED_TRACING_ENABLED=1`. Path is validated: must resolve inside CWD, `$HOME`, `tempfile.gettempdir()`, `/tmp`, or `/var/tmp`. Paths with `..` components or targets outside the whitelist log a WARNING and fall back to the default. |
| ALFRED_TRACE_STDOUT | off | Also emit a human-readable summary per span to stderr. Useful for live debugging. |
| ALFRED_PHASE1_DISABLED | - | Set to `1` to opt out of per-run MCP tracking state (budget cap, dedup cache, failure counter). Used only for benchmark comparisons against baseline. Leave unset in production. |

### Framework Knowledge Graph

The client app builds a framework KG (extracted DocType metadata from every
installed bench app) at `bench migrate` time and writes it to
`alfred_client/data/framework_kg.json`. This file is `.gitignore`d and
rebuilt on every migration. The `lookup_doctype(layer="framework")` tool
reads it. If you add a new Frappe app after Alfred is installed, run:

```bash
bench --site <your-site> migrate
```

to rebuild the KG with the new app's DocTypes. Missing KG file makes
`layer="framework"` return `{"error": "not_found"}` but the `layer="site"`
and merged `layer="both"` paths still work from live `frappe.get_meta()`.

---

## Troubleshooting

### "Processing App unreachable" when sending a message

**Cause**: The Frappe site can't reach the processing app via WebSocket.

**Fix**:
1. Verify the processing app is running: `curl http://localhost:8001/health`
2. Check the URL in Alfred Settings → Processing App URL
3. If Frappe and Docker are on different machines, use the server's IP instead of `localhost`
4. Check firewall allows port 8001
5. If behind nginx, add WebSocket proxy config (see below)

### "Not Authorized" when opening /app/alfred-chat

**Cause**: Your user role is not in the allowed list.

**Fix**:
1. Log in as Administrator
2. Go to `/app/alfred-settings` → Access Control tab
3. Add your role to the Allowed Roles table
4. Save

### Agents take too long / timeout

**Cause**: The LLM is slow (common with large models on CPU).

**Fix**:
1. Use a smaller model: set `FALLBACK_LLM_MODEL=ollama/llama3.2:3b`
2. Increase timeout in Alfred Settings → Limits → Task Timeout
3. Add GPU support (use `docker compose --profile local-llm --profile gpu up -d`)

### Deployment failed with "Permission Denied"

**Cause**: The requesting user doesn't have create permission on the target DocType type.

**Fix**: The user needs System Manager role, or the specific permissions required for the customization type (see Alfred Settings → Access Control).

### Ollama out of memory

**Cause**: The LLM model is too large for available RAM.

**Fix**:
1. Use a smaller model: `ollama pull llama3.2:3b` (3B params vs 8B)
2. Close other memory-intensive applications
3. Add swap space: `sudo fallocate -l 8G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
4. Enable GPU offloading if you have a GPU

### Nginx WebSocket Proxy Configuration

If your Frappe site is behind nginx and needs to reach the processing app:

```nginx
# Add to your nginx server block
location /alfred-ws/ {
    proxy_pass http://localhost:8001/ws/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 86400;
}
```

Then set Processing App URL to `ws://your-domain/alfred-ws` in Alfred Settings.

### Redis Connection Errors

```bash
# Check Redis is running
docker ps | grep redis

# Test Redis connectivity
docker exec -it $(docker ps -qf "name=redis") redis-cli ping
# Expected: PONG

# Check Redis URL in .env matches the actual Redis address
```

### Viewing Logs

```bash
# Processing app logs (all activity - WebSocket connections, agent pipeline, errors)
docker logs -f $(docker ps -qf "name=processing")

# Processing app logs - filter out health checks (cleaner view)
docker logs -f $(docker ps -qf "name=processing") 2>&1 | grep -v health

# Ollama logs (model loading, generation requests)
docker logs -f $(docker ps -qf "name=ollama")

# Frappe worker logs (default queue - background jobs, errors)
tail -f ~/frappe-bench/logs/worker.error.log

# Frappe long worker logs (connection manager runs here)
tail -f ~/frappe-bench/logs/worker_long.log
tail -f ~/frappe-bench/logs/worker_long.error.log

# Frappe worker activity (job starts, completions)
tail -f ~/frappe-bench/logs/worker.log

# Frappe web server logs (API calls, page loads)
tail -f ~/frappe-bench/logs/frappe.log
```

### Debugging a Conversation

If a conversation is stuck or not processing:

```bash
# 1. Check if the message was saved in the database
# Open: http://your-site:8000/app/alfred-message?conversation=<conversation-id>

# 2. Check conversation status
# Open: http://your-site:8000/app/alfred-conversation/<conversation-id>

# 3. Check if the connection manager started (Frappe worker log)
grep "<conversation-id>" ~/frappe-bench/logs/worker.log

# 4. Check if WebSocket connected to the processing app
docker logs alfred_processing-processing-1 2>&1 | grep -v health | tail -20

# 5. What you should see in processing app logs for a working conversation:
#    alfred.websocket INFO: WebSocket connection opened: conversation=<id>
#    alfred.websocket INFO: WebSocket authenticated: user=..., site=...
#    alfred.websocket INFO: Custom message from ...: type=prompt
#    alfred.websocket INFO: Running agent pipeline...
#
# 6. If you see "connection open" but NOT "WebSocket authenticated":
#    - The API key in Alfred Settings doesn't match API_SECRET_KEY in .env
#    - Check both values match exactly
#
# 7. If you see nothing after health checks:
#    - The Frappe worker didn't start the connection manager
#    - Check: grep "connection_manager" ~/frappe-bench/logs/worker.log
#    - Make sure bench workers are running: bench start (or bench restart)
#    - Redis must be running (required for background jobs)
```

### Messages queued but never reach the Processing App

**Symptom**: Health button shows `redis_queue_depth: 1` (stuck), message never arrives at the Processing App.

**Cause 1 - No long queue worker**: The Procfile doesn't have a `worker_long` entry. The `_connection_manager` job is enqueued to `queue="long"` but nothing processes it.

**Fix**: Add the `worker_long` line to your Procfile (see [Part C, step 2](#2-add-a-long-queue-worker-required)) and restart bench.

**Cause 2 - Redis instance mismatch**: `send_message()` writes to `redis_cache` (port 13000) but the connection manager reads from a different Redis instance.

**Fix**: The connection manager must connect to `frappe.conf.get("redis_cache")`. This is already handled in the code - if you're seeing this, check `common_site_config.json` for the correct `redis_cache` URL.

### LLM connection refused / timeout

**Symptom**: Processing app logs show `litellm.APIConnectionError: OllamaException - [Errno 61] Connection refused` or `litellm.Timeout`.

**Cause**: The LLM endpoint (Ollama or cloud provider) is unreachable from the Processing App.

**Fix**:
1. Use **Alfred Settings → Actions → Test LLM Connection** to diagnose
2. For Ollama: ensure `ollama serve` is running and the Base URL is correct
3. For remote Ollama: verify network connectivity with `curl http://your-server:11434/api/tags`
4. For cloud providers: verify the API key is valid and not expired
5. Use `test_llm.py` on the Processing App server for detailed diagnostics: `.venv/bin/python test_llm.py`

### Common Status Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Processing..." forever, no response | Connection manager didn't start or WebSocket auth failed | Check worker.log for errors, verify API key matches |
| Shows "Ready" after refresh | No persisted status - the pipeline didn't update the conversation | Check processing app logs for errors |
| Message saved but nothing happens | No `long` queue worker in Procfile | Add `worker_long` to Procfile (see Part C step 2) |
| Message queued, depth stays at 1 | Redis instance mismatch (cache vs queue) | Check connection manager uses `redis_cache` (port 13000) |
| WebSocket connects but no "authenticated" log | JWT signing key mismatch | Ensure API Key in Alfred Settings = API_SECRET_KEY in .env |
| LLM Failed / Connection refused | Ollama not running or unreachable | Use Actions → Test LLM Connection in Alfred Settings |

---

## LLM Provider Configuration

Alfred supports Ollama (local or remote) and cloud providers (Anthropic, OpenAI, Gemini, Bedrock). You can switch providers at any time - each new conversation uses the current configuration.

### Ollama (Local and Remote)

Ollama runs open-source models. It works identically whether running locally on the same machine or on a remote server.

**Model name auto-prefix**: You can type just `codegemma:7b` - Alfred automatically converts it to `ollama/codegemma:7b` on save (required by LiteLLM for routing).

#### Local Ollama (same machine as Processing App)

| Field | Value |
|-------|-------|
| LLM Provider | `ollama` |
| LLM Model | `qwen2.5-coder:7b` (auto-prefixed to `ollama/qwen2.5-coder:7b`) |
| LLM API Key | *(leave empty)* |
| LLM Base URL | `http://localhost:11434` |

Processing app `.env`:
```env
FALLBACK_LLM_MODEL=ollama/qwen2.5-coder:7b
FALLBACK_LLM_BASE_URL=http://localhost:11434
```

For GPU servers with >16 GB VRAM, upgrade to `qwen2.5-coder:14b` or
`:32b` and configure per-tier overrides in Alfred Settings. Full
sizing guide: [admin-guide.md > Recommended Ollama models](admin-guide.md#recommended-ollama-models).

#### Remote Ollama (separate server, accessed via network)

If Ollama runs on a different machine (e.g., a GPU server), point the Base URL to that server's IP:

| Field | Value |
|-------|-------|
| LLM Provider | `ollama` |
| LLM Model | `qwen2.5-coder:14b` (auto-prefixed to `ollama/qwen2.5-coder:14b`) |
| LLM API Key | *(leave empty, unless your proxy requires auth)* |
| LLM Base URL | `http://135.13.20.57:11434` (your server's IP and port) |

Processing app `.env`:
```env
FALLBACK_LLM_MODEL=ollama/codegemma:7b
FALLBACK_LLM_BASE_URL=http://135.13.20.57:11434
```

**Verify the remote Ollama is reachable**:
```bash
curl http://135.13.20.57:11434/api/tags
# Should return a JSON list of installed models

curl http://135.13.20.57:11434/api/generate -H "Content-Type: application/json" \
  -d '{"model":"codegemma:7b","prompt":"Hello","stream":false}'
# Should return a JSON response with generated text
```

#### Ollama via API Proxy / Jump Server

If Ollama is behind a proxy or only reachable through a jump server with a custom endpoint:

| Field | Value |
|-------|-------|
| LLM Provider | `ollama` |
| LLM Model | `codegemma:7b` |
| LLM API Key | *(set if your proxy requires an auth token)* |
| LLM Base URL | `http://your-proxy-host:port` (wherever the `/api/generate` endpoint is exposed) |

The only requirement is that the Base URL responds to Ollama's HTTP API format (`/api/generate`, `/api/tags`, etc.).

#### Testing the Connection

**From the UI**: Open `/app/alfred-settings` → **Actions** → **Test LLM Connection**. This checks reachability, model availability, and sends a test generation request.

**From the CLI** (on the Processing App server):
```bash
# Quick connectivity check
curl http://your-ollama-host:11434/api/tags

# Full end-to-end test with streaming (uses .env config)
cd alfred_processing
.venv/bin/python test_llm.py

# Override model/URL inline
.venv/bin/python test_llm.py --model ollama/llama3.2:3b --base-url http://localhost:11434
```

**Pricing**: Free. No API keys. No usage limits. Only cost is the hardware running Ollama.

---

### Cloud LLM Providers

Cloud providers give faster and often better results than local Ollama, at the cost of sending data to an external API.

### Anthropic Claude

1. Get an API key at https://console.anthropic.com/settings/keys
2. In Alfred Settings → LLM Configuration:

| Field | Value |
|-------|-------|
| LLM Provider | `anthropic` |
| LLM Model | `claude-sonnet-4-20250514` |
| LLM API Key | `sk-ant-api03-...` (your key) |
| LLM Base URL | *(leave empty)* |
| Max Tokens | `4096` |
| Temperature | `0.1` |

3. Also set the fallback in processing app `.env` (for when client doesn't send config):
```env
FALLBACK_LLM_MODEL=anthropic/claude-sonnet-4-20250514
FALLBACK_LLM_API_KEY=sk-ant-api03-your-key-here
```

**Pricing**: ~$3 per million tokens. A typical conversation uses 5,000-30,000 tokens (~$0.01-$0.09).

### OpenAI GPT

1. Get an API key at https://platform.openai.com/api-keys
2. In Alfred Settings:

| Field | Value |
|-------|-------|
| LLM Provider | `openai` |
| LLM Model | `gpt-4o` |
| LLM API Key | `sk-proj-...` (your key) |
| LLM Base URL | *(leave empty)* |

**Pricing**: ~$2.50 per million tokens.

### Google Gemini

1. Get an API key at https://aistudio.google.com/app/apikey
2. In Alfred Settings:

| Field | Value |
|-------|-------|
| LLM Provider | `gemini` |
| LLM Model | `gemini/gemini-2.0-flash` |
| LLM API Key | `AIza...` (your key) |
| LLM Base URL | *(leave empty)* |

**Pricing**: ~$1.25 per million tokens.

### AWS Bedrock

1. Configure AWS credentials on the processing app server (`~/.aws/credentials` or IAM role)
2. In Alfred Settings:

| Field | Value |
|-------|-------|
| LLM Provider | `bedrock` |
| LLM Model | `bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0` |
| LLM API Key | *(leave empty - uses AWS credentials)* |
| LLM Base URL | *(leave empty)* |

### Switching Providers

You can switch providers at any time by updating Alfred Settings. Each new conversation uses the current configuration. Existing conversations keep the config they started with.

### Provider Comparison

| Provider | Speed | Quality | Cost | Privacy |
|----------|-------|---------|------|---------|
| Ollama (local) | Slow on CPU, fast with GPU | Good (llama3.1) | Free | Full - nothing leaves your server |
| Anthropic Claude | Fast | Excellent | ~$3/M tokens | Data sent to Anthropic API |
| OpenAI GPT | Fast | Excellent | ~$2.50/M tokens | Data sent to OpenAI API |
| Google Gemini | Fast | Good | ~$1.25/M tokens | Data sent to Google API |
| AWS Bedrock | Fast | Excellent | ~$3/M tokens | Data stays in your AWS account |

---

## Production Deployment

For running Alfred in production with real users, beyond the dev setup.

### Separate the Components

In production, the three components typically run on different servers or services:

```
┌─────────────────────────┐     ┌──────────────────────────────────┐
│  Customer's Frappe Site  │     │  Your Infrastructure             │
│  (Frappe Cloud / VPS)    │────▶│                                  │
│  alfred_client installed │     │  Processing App (Docker)         │
└─────────────────────────┘     │  + Redis + Ollama/Cloud LLM      │
                                └──────────────────────────────────┘
                                              │
                                ┌──────────────────────────────────┐
                                │  Admin Portal (optional)          │
                                │  (Frappe Cloud / VPS)             │
                                │  alfred_admin installed            │
                                └──────────────────────────────────┘
```

### SSL/TLS for WebSocket

Production WebSocket connections must use `wss://` (encrypted), not `ws://`.

**Option A: Let nginx handle SSL**

```nginx
server {
    listen 443 ssl;
    server_name alfred-api.yourcompany.com;

    ssl_certificate /etc/letsencrypt/live/alfred-api.yourcompany.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/alfred-api.yourcompany.com/privkey.pem;

    # REST API
    location / {
        proxy_pass http://localhost:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://localhost:8001/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
```

Then in Alfred Settings:
- Processing App URL: `wss://alfred-api.yourcompany.com`

**Option B: Cloudflare Tunnel** (no port exposure needed)

```bash
cloudflared tunnel --url http://localhost:8001
```

### Production Environment Variables

```env
# Strong secret - generate with: python3 -c "import secrets; print(secrets.token_urlsafe(64))"
API_SECRET_KEY=<64-char-random-string>

# Redis with password
REDIS_URL=redis://:your-redis-password@redis:6379/0

# CORS - restrict to your customer's sites
ALLOWED_ORIGINS=https://customer1.frappe.cloud,https://customer2.example.com

# Workers - 2 per CPU core
WORKERS=8

# Disable debug
DEBUG=false

# Cloud LLM (production usually uses Claude or GPT, not Ollama)
FALLBACK_LLM_MODEL=anthropic/claude-sonnet-4-20250514
FALLBACK_LLM_API_KEY=sk-ant-api03-your-production-key
```

### Systemd Service (alternative to Docker)

If you prefer running the processing app natively:

```ini
# /etc/systemd/system/alfred-processing.service
[Unit]
Description=Alfred Processing App
After=network.target redis.service

[Service]
User=alfred
Group=alfred
WorkingDirectory=/opt/alfred_processing
EnvironmentFile=/opt/alfred_processing/.env
ExecStart=/opt/alfred_processing/.venv/bin/uvicorn alfred.main:app --host 0.0.0.0 --port 8001 --workers 4
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable alfred-processing
sudo systemctl start alfred-processing
```

---

## Updating Alfred

### Updating the Client App

```bash
cd frappe-bench

# Pull latest code
cd apps/alfred_client
git pull origin main
cd ../..

# Run migrations (updates DocTypes, adds new fields)
bench --site your-site migrate

# Rebuild frontend assets
bench build --app alfred_client

# Restart
bench restart
```

### Updating the Processing App

```bash
cd alfred_processing

# Pull latest code
git pull origin main

# Rebuild and restart Docker containers
docker compose --profile local-llm up -d --build

# Or if running natively:
.venv/bin/pip install -e .
sudo systemctl restart alfred-processing
```

### Updating the Admin Portal

```bash
cd frappe-bench
cd apps/alfred_admin
git pull origin main
cd ../..
bench --site your-admin-site migrate
bench restart
```

### Version Compatibility

Always update all three components together. The client app and processing app communicate via WebSocket and must be on compatible versions. Check the release notes for any breaking changes.

---

## Backup & Recovery

### What to Back Up

| Data | Location | How | Frequency |
|------|----------|-----|-----------|
| Conversations & messages | Frappe database | `bench --site your-site backup` | Daily |
| Changesets & audit logs | Frappe database | Same as above | Daily |
| Alfred Settings | Frappe database | Same as above | After changes |
| Processing app config | `.env` file | Copy to backup location | After changes |
| Redis state | Redis RDB dump | `docker exec redis redis-cli BGSAVE` | Hourly (optional) |
| Created DocTypes | Frappe database + JSON files | `bench --site your-site backup` | Daily |

### Automated Backup

Frappe includes built-in backup scheduling. Enable it at:
`/app/system-settings` → Backup section → Enable automatic backups

### Recovery

```bash
# Restore Frappe site from backup
bench --site your-site restore /path/to/backup.sql.gz

# Run migration after restore
bench --site your-site migrate
```

### What Happens If You Lose Redis State

Redis holds temporary data: active crew state, WebSocket message buffers, rate limit counters, plan cache. If Redis is lost:
- **Active conversations in progress** will need to be restarted (crew state is lost)
- **Completed conversations** are unaffected (stored in Frappe database)
- **Rate limit counters** reset (users may get a brief burst allowance)
- **No permanent data is lost** - Redis is a cache, not the source of truth

---

## Monitoring

### Health Checks

Set up monitoring on these endpoints:

| Endpoint | Expected | Check Interval |
|----------|----------|---------------|
| `GET http://processing-app:8001/health` | `{"status": "ok", "redis": "connected"}` | 30 seconds |
| `GET http://your-site:8000/api/method/ping` | `{"message": "pong"}` | 30 seconds |
| `http://ollama:11434/api/tags` | JSON with model list | 5 minutes |

### Key Metrics to Watch

| Metric | How to Check | Warning Threshold |
|--------|-------------|-------------------|
| Redis memory | `docker exec redis redis-cli INFO memory \| grep used_memory_human` | > 200 MB |
| Active WebSocket connections | `docker logs processing \| grep "WebSocket authenticated" \| wc -l` | > 100 concurrent |
| Agent timeouts | `docker logs processing \| grep "Pipeline timeout"` | > 5 per hour |
| Failed deployments | Frappe: count Alfred Changesets with status "Rolled Back" | > 3 per day |
| Escalated conversations | Frappe: count Alfred Conversations with status "Escalated" | > 10 per day |
| LLM error rate | `docker logs processing \| grep "LLM" \| grep -i "error\|timeout"` | > 10% of requests |
| Redis stream growth | `docker exec redis redis-cli DBSIZE` | > 50,000 keys |

### Log Rotation

Docker logs grow indefinitely by default. Add to `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
```

Then restart Docker: `sudo systemctl restart docker`

### Alerting

Set up alerts using your preferred monitoring tool (Uptime Kuma, Grafana, Datadog, etc.) on:

1. **Processing app health check fails** → Critical: agents can't run
2. **Redis disconnected** → Warning: new conversations will fail, existing ones lose state
3. **Ollama not responding** → Critical (if using local LLM): all conversations will fail
4. **Disk space < 10%** → Warning: Ollama models and Redis dumps need disk space
5. **Error rate spikes** → Warning: may indicate LLM issues or site connectivity problems
