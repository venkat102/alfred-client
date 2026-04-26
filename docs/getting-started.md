# Getting Started with Alfred

This is the **single doc you read to install Alfred and have your first chat with it.** It covers:

1. **Quick Start** — the 5-minute path, for the impatient
2. **Detailed install** — full walkthrough (Frappe site + Processing App + Configuration)
3. **Self-hosted deployment** — Docker quickstart for running your own Processing App
4. **Initial configuration** — Alfred Settings + admin portal config
5. **Your first chat** — a complete walkthrough from "send prompt" to "deployed change"

Once you have Alfred running and you've sent your first prompt, the next docs to read are:

- [`how-it-works.md`](how-it-works.md) — concepts, architecture, data flow
- [`developing.md`](developing.md) — making changes to Alfred itself
- [`running.md`](running.md) — operating Alfred in production
- [`SECURITY.md`](SECURITY.md) — threat model + production checklist

---

# Part 1 — Install + Configure


Everything you need to go from zero to a working Alfred installation. Follow these steps in order.

> **New here?** This is the right document. Read this first to set up Alfred, then see the [User Guide](getting-started.md) for detailed usage instructions.
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
# Edit .env: set API_SECRET_KEY. Easiest is the rotation script, which
# also backs up .env and prints follow-up steps:
#     python scripts/rotate_api_secret_key.py
# Or generate one by hand: python3 -c "import secrets; print(secrets.token_urlsafe(48))"
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
# prose with generic chat models. See getting-started.md > "Recommended
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
in Alfred Settings (see `getting-started.md` > "Recommended Ollama models"):

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
| LLM Model | `qwen2.5-coder:7b` (or `:14b` / `:32b` if you have the VRAM) | Must be a coder-tuned model. Auto-prefixed to `ollama/qwen2.5-coder:7b` on save. See [getting-started.md > Recommended Ollama models](getting-started.md#recommended-ollama-models) for tier-based setup. |
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

Setup is complete. For the full usage guide - including step-by-step conversation walkthrough, what each screen element means, how to handle errors, escalation, rollback, and tips for writing better prompts - see the **[User Guide](getting-started.md)**.

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
| mcp_timeout | Int | 30 | Per-tool-call timeout (seconds) for MCP requests from the processing app back to the Frappe site. Increase when `lookup_doctype` / `run_query` routinely take longer than 30s on your site. Set to 0 to fall back to the processing-side default (also 30). Negative / non-integer values fall back to 30 defensively. |
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
| API_SECRET_KEY | Yes | - | JWT signing key + API auth key. Must be at least 32 characters. The processing app refuses to boot on shorter keys or known-weak placeholders (`secret`, `changeme`, ...). Rotate with `python scripts/rotate_api_secret_key.py`. |
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
| ALFRED_ORCHESTRATOR_ENABLED | off | Enable the three-mode chat orchestrator. When on, prompts are classified into `dev` / `plan` / `insights` / `chat` and conversational / read-only turns short-circuit the crew. See the three-mode chat section of `how-it-works.md` for the full flow. `1` to enable. |
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
sizing guide: [getting-started.md > Recommended Ollama models](getting-started.md#recommended-ollama-models).

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

---

# Part 2 — Self-Hosted Deployment

If you're running the Processing App yourself (not using a hosted Alfred service), this is the Docker quickstart. Skip this section if you're using a managed Processing App.


## Prerequisites

- Docker + Docker Compose installed
- A Frappe v15+ site with `alfred_client` app installed
- At least 8GB RAM (16GB recommended for Ollama)
- GPU optional (CPU works for smaller models, GPU recommended for production)

## Step 1: Clone and Configure

```bash
git clone <your-alfred-processing-repo>
cd alfred_processing
cp .env.example .env
```

Edit `.env`:
```env
API_SECRET_KEY=<generate-a-strong-random-key>
REDIS_URL=redis://redis:6379/0
FALLBACK_LLM_MODEL=ollama/llama3.1
FALLBACK_LLM_BASE_URL=http://ollama:11434

# Optional feature flags
# ALFRED_ORCHESTRATOR_ENABLED=1        # three-mode chat: dev / plan / insights / chat (see how-it-works.md)
# ALFRED_REFLECTION_ENABLED=1          # trim agent over-reach from the changeset
# ALFRED_TRACING_ENABLED=1             # per-phase JSONL span tracing
# ALFRED_TRACE_PATH=./alfred_trace.jsonl
# ALFRED_TRACE_STDOUT=1                # also echo spans to stderr
```

See the [Admin Guide](getting-started.md#part-3-processing-app-configuration) for
the full environment variable reference.

## Step 2: Start Services

```bash
# With local Ollama:
docker compose --profile local-llm up -d

# Or without Ollama (if using remote Ollama or cloud LLM):
docker compose up -d
```

This starts:
- **Processing App** (port 8001) - the AI agent service
- **Redis** (port 6379) - state store
- **Ollama** (port 11434) - local LLM server

## Step 3: Pull an LLM Model

```bash
docker exec -it alfred_processing-ollama-1 ollama pull llama3.1
```

For smaller hardware, use `llama3.2:3b` or `mistral:7b`.

## Step 4: Verify Health

```bash
curl http://localhost:8001/health
# Expected: {"status": "ok", "version": "0.1.0", "redis": "connected"}
```

## Step 5: Configure Alfred Settings

On your Frappe site, go to `/app/alfred-settings`:

| Field | Value |
|-------|-------|
| Processing App URL | `ws://localhost:8001` (or your server's IP) |
| API Key | Same value as `API_SECRET_KEY` in `.env` |
| LLM Provider | `ollama` |
| LLM Model | `ollama/llama3.1` |
| LLM Base URL | `http://localhost:11434` |

## Step 6: Test

Navigate to `/app/alfred-chat` and type: "Create a DocType called Book with title and author fields"

## Troubleshooting

**Ollama out of memory**: Use a smaller model (`llama3.2:3b`) or add GPU support with `docker compose --profile local-llm --profile gpu up -d`.

**Processing App unreachable**: Check that your Frappe site can reach `localhost:8001`. If running in Docker, use the host's IP instead of `localhost`.

**Redis connection refused**: Ensure the Redis container is running: `docker ps | grep redis`.

**WebSocket connection fails**: Check firewall allows port 8001. If behind nginx, add WebSocket proxy:
```nginx
location /ws/ {
    proxy_pass http://localhost:8001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
}
```

---

# Part 3 — Admin Configuration

Once Alfred is installed, the **Alfred Settings** page (`/app/alfred-settings`) and the **admin portal** carry the day-to-day configuration. This section is for site admins.


## Part 1: Customer Site Configuration (Alfred Settings)

Access at `/app/alfred-settings`. Only System Managers can configure.

### Connection Tab

| Field | Description | Default |
|-------|-------------|---------|
| Processing App URL | WebSocket URL of the processing service | - |
| API Key | Shared secret for authentication (encrypted) | - |
| Self-Hosted Mode | Enable when running your own processing app | Off |
| Redis URL | Redis connection (self-hosted only) | - |
| Pipeline Mode | `full` (6-agent SDLC, ~5-10 min, highest quality) or `lite` (single-agent fast pass, ~1 min, ~5× cheaper, best for simple customizations). Overridden by admin portal plan when configured. | `full` |

**Pipeline Mode precedence** (highest to lowest):
1. Admin portal `check_plan` response `pipeline_mode` field - lets SaaS plans lock lower tiers to lite
2. `Alfred Settings.pipeline_mode` - self-hosted / no-portal installs
3. Default `full`

When a plan forces lite, the "Basic" badge in the chat UI tooltip says *"set by
your subscription plan - upgrade to unlock the full 6-agent pipeline."* When
it's set locally, the tooltip says *"configured in Alfred Settings."*

### LLM Configuration Tab

| Field | Description | Default |
|-------|-------------|---------|
| LLM Provider | ollama, anthropic, openai, gemini, bedrock | - |
| LLM Model | Default model. Name only (e.g., `qwen2.5-coder:14b`); auto-prefixed with provider on save. Used for tiers that don't have an override. | - |
| LLM API Key | Provider API key. Optional for Ollama (needed if proxy requires auth). | - |
| LLM Base URL | Endpoint URL. Local Ollama: `http://localhost:11434`. Remote: `http://server-ip:11434`. Empty for cloud. | - |
| Max Tokens | Max tokens per LLM response | 4096 |
| Temperature | Generation randomness (0.0-2.0) | 0.1 |
| **Per-Stage Model Overrides** (optional, Ollama only) | | |
| LLM Model (Triage) | Small fast model for classifier / chat / reflection. Empty = use default. | - |
| LLM Model (Reasoning) | Medium model for enhancer / clarifier / rescue. Empty = use default. | - |
| LLM Model (Agent) | Strongest coder model for the SDLC crew + Plan + Insights. Empty = use default. | - |

See [Recommended Ollama models](#recommended-ollama-models) below for concrete picks per tier + VRAM sizing.

### Recommended Ollama models

Alfred routes LLM calls to three tiers. Set a model per tier in **LLM
Configuration > Per-Stage Model Overrides** to trade off latency vs
quality per stage. Empty tier fields fall back to the default `LLM Model`
(single-model deployments keep working unchanged).

**What each tier does:**

- **Triage** runs the orchestrator classifier, chat handler, and
  reflection. Short structured JSON, <256 tokens, temperature 0. Hot
  path - speed matters more than smarts. Does NOT need a coder model.
- **Reasoning** runs prompt enhancement, clarification questions, and
  the rescue-regenerate path. 512-2048 tokens, domain reasoning about
  Frappe schemas. Instruction-following matters here.
- **Agent** runs the full SDLC crew (6 agents) + Plan crew + Insights
  + Lite. Tool use + JSON/code generation, up to 4096 tokens per agent
  turn, longest runs. **Must be a coder-tuned model** or the Developer
  agent drifts into prose.

**Preset: Budget (~8 GB VRAM total if loaded together)**

| Tier | Model | VRAM (Q4) | Notes |
|---|---|---|---|
| Triage | `ollama/llama3.2:3b` or `ollama/qwen2.5:3b` | ~2 GB | Sub-second classifier. Cheapest viable. |
| Reasoning | `ollama/qwen2.5:7b` | ~5 GB | Decent instruction-following. |
| Agent | `ollama/qwen2.5-coder:7b` | ~5 GB | Will drift more than 14B. Expect a few rescue events per 10 builds. |

**Preset: Balanced (~24 GB VRAM recommended)**

| Tier | Model | VRAM (Q4) | Notes |
|---|---|---|---|
| Triage | `ollama/gemma2:9b` or `ollama/qwen2.5:7b` | ~5-6 GB | Fast + accurate JSON. |
| Reasoning | `ollama/qwen2.5:14b` | ~9 GB | Sweet spot for Frappe domain reasoning. |
| Agent | `ollama/qwen2.5-coder:14b` | ~9 GB | Solid SDLC. Occasional rescue. |

**Preset: Premium (~48 GB VRAM recommended, or remote/cloud)**

| Tier | Model | VRAM (Q4) | Notes |
|---|---|---|---|
| Triage | `ollama/qwen2.5:14b` | ~9 GB | Still fast, cleanest JSON. |
| Reasoning | `ollama/qwen2.5:32b` or `ollama/mistral-small:22b` | ~14-20 GB | Best enhancement quality. |
| Agent | `ollama/qwen2.5-coder:32b` | ~20 GB | Current top-end open coder. Baseline for "production" deployments. |

**Pulling the models:**

```bash
# Balanced preset, one-liner
ollama pull qwen2.5:7b \
  && ollama pull qwen2.5:14b \
  && ollama pull qwen2.5-coder:14b
```

**Notes that actually matter:**

- The Agent tier **must be coder-tuned**. `llama3.1:8b` or `gemma2:27b`
  in the Agent slot will work but drift significantly more, driving up
  `alfred_crew_drift_total` and `alfred_crew_rescue_total`. Watch those
  metrics after a model change.
- VRAM estimates are for Q4_K_M (Ollama's default). Q8 roughly doubles,
  fp16 roughly quadruples.
- Ollama loads models on first request and holds them for 5 min by
  default. Alfred's `warmup` pipeline phase pre-pulls tier models with
  `keep_alive=10m` - but only when >1 distinct model is configured. If
  you set the same model in all three tier slots, warmup is a no-op and
  the first turn of a cold session pays the load cost.
- For cloud providers (Anthropic / OpenAI / Gemini / Bedrock), set
  `llm_model` to the provider-prefixed id (e.g. `anthropic/claude-sonnet-4-20250514`)
  and leave the per-tier overrides empty. Per-tier routing is an
  Ollama-only feature today.
- After a model change, flush the `warmup` metrics by running one Dev
  prompt and watching the `/metrics` output - drift/rescue counters
  tell you within a day whether the new models are holding up.

### Access Control Tab

| Field | Description | Default |
|-------|-------------|---------|
| Allowed Roles | Roles permitted to use Alfred | System Manager |
| Enable Auto Deploy | Skip manual approval for changesets | Off |

### Limits Tab

| Field | Description | Default |
|-------|-------------|---------|
| Max Retries Per Agent | Retry loops before escalation | 3 |
| Max Tasks Per User Per Hour | Rate limit | 20 |
| Task Timeout (seconds) | Max time per agent task | 300 |
| Stale Conversation Hours | Mark inactive conversations stale | 24 |

### Usage Tab (Read-Only)
- Total Tokens Used
- Total Conversations

---

## Part 2: Admin Portal Configuration

The Admin Portal (`alfred_admin` app) is installed on your management site.

### Alfred Admin Settings

| Field | Description | Default |
|-------|-------------|---------|
| Grace Period Days | Days after payment failure before suspension | 7 |
| Default Plan | Auto-assigned plan for new customers | - |
| Warning Threshold % | Token usage percentage that triggers warning | 80 |
| Trial Duration Days | Default trial length | 14 |
| Service API Key | Authentication key for Processing App | - |

### Alfred Plan

Define subscription tiers:
- **Plan Name**: Display name (e.g., "Free", "Pro", "Enterprise")
- **Monthly Price**: Subscription cost
- **Monthly Token Limit**: Max tokens per month
- **Monthly Conversation Limit**: Max conversations per month
- **Max Users**: Concurrent users allowed
- **Pipeline Mode** (required): `full` (6-agent SDLC, highest quality) or
  `lite` (single-agent fast pass, ~5x cheaper, ~5x faster). This is the
  **tier-locked** mode returned by `check_plan` to the processing app,
  overriding whatever the site's local `Alfred Settings.pipeline_mode` says.
  Use this to lock starter plans to `lite` and unlock `full` on higher tiers.
  Defaults to `full` for new plans.
- **Features**: Child table of included features

### Alfred Customer

Each customer site gets a record:
- **Site ID**: Canonical site URL (e.g., `company.frappe.cloud`)
- **Current Plan**: Linked to Alfred Plan
- **Status**: Active / Suspended / Cancelled
- **Override Limits**: Admin can temporarily remove limits
- **Trial dates**: Start and end of trial period

### Alfred Subscription

Tracks billing subscriptions with status lifecycle:
`Trial → Active → Past Due → Cancelled / Expired`

---

## Part 3: Processing App Configuration

Environment variables (set in `.env` or Docker):

**Core**

| Variable | Required | Description |
|----------|----------|-------------|
| `API_SECRET_KEY` | Yes | Shared secret for JWT signing |
| `REDIS_URL` | Yes | Redis connection URL |
| `HOST` | No | Bind address (default: 0.0.0.0) |
| `PORT` | No | Port (default: 8000) |
| `WORKERS` | No | Uvicorn workers (default: 4) |
| `FALLBACK_LLM_MODEL` | No | Default LLM when client doesn't specify |
| `FALLBACK_LLM_API_KEY` | No | API key for fallback LLM |
| `FALLBACK_LLM_BASE_URL` | No | Base URL for fallback LLM |
| `ADMIN_PORTAL_URL` | No | Admin portal URL (SaaS mode) |
| `ADMIN_SERVICE_KEY` | No | Admin portal service API key |
| `DEBUG` | No | Enable debug mode (default: false) |

**Feature flags** (all optional, all default off)

| Variable | Default | Description |
|----------|---------|-------------|
| `ALFRED_ORCHESTRATOR_ENABLED` | off | Enable the three-mode chat orchestrator. When on, every prompt is classified into `dev` / `plan` / `insights` / `chat`. Conversational prompts (greetings, thanks, meta questions) are short-circuited to a fast chat handler, read-only queries (*"what DocTypes do I have?"*) are routed to an Insights-mode single-agent crew with a 5-call read-only MCP tool budget, and build requests run the full 6-agent SDLC pipeline as before. `1` to enable. See `docs/how-it-works.md` for the full flow. |
| `ALFRED_REFLECTION_ENABLED` | off | Enable the post-crew minimality reflection step that drops items the user didn't ask for. `1`/`true`/`yes` to enable. Off by default for cautious rollout - once enabled, you'll see `minimality_review` events in the chat UI when the reviewer trims an over-reaching changeset. |
| `ALFRED_TRACING_ENABLED` | off | Enable structured span tracing. Emits one JSON object per pipeline phase to `ALFRED_TRACE_PATH`. |
| `ALFRED_TRACE_PATH` | `./alfred_trace.jsonl` | JSONL output location for tracer spans. Only relevant when `ALFRED_TRACING_ENABLED=1`. |
| `ALFRED_TRACE_STDOUT` | off | Also emit a human-readable summary line to stderr per span. Useful during live debugging. |
| `ALFRED_PHASE1_DISABLED` | off | Set to `1` to opt out of the per-run MCP tracking state (budget cap, dedup cache, failure counter). Use only for A/B benchmark comparisons against a pre-hardening baseline. Leave unset in production. |

> **Terminology note**: The codebase uses two orthogonal phase taxonomies. *Phase 1 / Phase 2 / Phase 3* refer to the architecture-improvement work (tool hardening, handoff condenser, state machine, reflection, tracer). *Phase A / Phase B / Phase C / Phase D* refer to the three-mode chat rollout (chat handler + sanitizer fix -> insights mode -> plan mode + cross-mode handoff -> UI mode switcher). All four phases are now shipped. The env var names above don't encode either - they describe what the flag does, not when it shipped.

---

## Troubleshooting

**"Processing App unreachable"**
- Check that the Processing App URL in Alfred Settings is correct (ws:// or wss://)
- Verify the Processing App is running: `curl http://processing-host:8001/health`
- Check firewall rules allow WebSocket connections

**"Permission denied" when using Alfred**
- Verify your role is in Alfred Settings > Allowed Roles
- If empty, only System Manager and Custom Field creators have access

**Agent keeps looping without progress**
- Max retries might be too high - reduce in Alfred Settings > Limits
- Check the LLM configuration - ensure the model is responsive
- Review the conversation for ambiguous requirements

**Deployment failed with rollback**
- Check the Alfred Changeset > Deployment Log for the specific error
- Common causes: naming conflict, permission revoked, DocType already exists

**Escalated conversations not receiving notifications**
- Verify email is configured in Frappe (Setup > Email Account)
- Check that System Managers have valid email addresses

---

# Part 4 — Your First Chat

You have Alfred installed and configured. This section walks you through your first complete conversation: sending a prompt, watching the agents work, reviewing the changeset, approving deployment, and (if you change your mind) rolling back.


A complete guide to using Alfred - from your first conversation to deployment and rollback.

> **Haven't set up Alfred yet?** Read the [Setup Guide](getting-started.md) first. This guide assumes Alfred is already installed and configured on your Frappe site.

---

## What is Alfred?

Alfred is an AI assistant that builds Frappe customizations through conversation. Describe what you need - a DocType, workflow, report, or automation - and Alfred designs it, generates the code, validates it, and deploys it to your site after your approval.

### What Alfred Can Build
- **DocTypes** - New document types with fields, permissions, naming rules, and child tables
- **Custom Fields** - New fields on existing DocTypes (like adding phone_number to Customer)
- **Server Scripts** - Python automation that runs on save, submit, or via API
- **Client Scripts** - JavaScript that customizes forms (filters, calculated fields, visibility)
- **Workflows** - Multi-state approval processes with role-based transitions
- **Notifications** - Email/SMS alerts triggered by document events
- **Reports** - Custom reports with filters and columns
- **Print Formats** - PDF templates for documents

### What Alfred Cannot Do
- Modify core Frappe or ERPNext source code files
- Run bench commands or shell operations
- Access your server's file system
- Make changes requiring app-level Python files (hooks.py, custom app code)
- Build features that don't exist in Frappe's customization framework (like real-time dashboards or external API integrations)

---

## What the interface looks like

Alfred Chat is a conversation-first shell. Your transcript fills the
page; everything else (status, preview, navigation) floats on top so
the chat is never cropped or compressed.

- **Frosted topbar** (48px, sticky). Back button, a small gradient
  "A" mark + the conversation title, the mode switcher in the
  center, and the right zone for the preview toggle, "+ New", and
  the overflow menu (Health / Share / Delete). Frappe's empty
  breadcrumb strip above the page head is hidden so the topbar is
  the only chrome you see.
- **Floating status pill** centered near the top of the transcript.
  Idle state shows a small green dot with "Ready". While a run is
  processing, the pill switches to a pulsing chat-gradient mark +
  the current agent name + a live activity phrase ("Developer -
  generating code"). Click the pill to expand a popover with the
  full six-step pipeline trail. When a run ends, the pill briefly
  flashes green (completed) or red (failed) for ~4 seconds and
  then settles back to idle.
- **Centered composer** floats at the bottom of the chat, max-width
  760px. Gradient Send button with an arrow that slides right on
  hover; ghost-red Stop button that takes over during a run.
  Keyboard hints sit below: `Enter` to send, `Shift+Enter` for a
  newline, and `Cmd/Ctrl+Enter` also sends.
- **Slide-in preview drawer** from the right edge, 420px wide. It
  auto-opens when a changeset arrives and the toolbar toggle shows
  a red dot until you do. Click the toggle (or press Escape when
  no other surface is open) to close; the drawer minimizes to a
  floating chip at the bottom-right showing the change count, and
  clicking the chip reopens the drawer. The drawer state is
  persisted across reloads. On mobile the drawer becomes a
  full-screen modal with a dimming scrim.
- **Mode chips + tone banners + step trails** still anchor the
  visual language - mode chips use the four mode colors (auto,
  dev, plan, insights), banners use tone colors (success green,
  info blue, warn orange, danger red, neutral gray), and step
  trails share one dot + pulse vocabulary across the transcript
  and the preview deploy stream.

Screenshots (captured from a live session):

![Welcome state](images/alfred-welcome.png)
![Mid-run with status pill](images/alfred-working.png)
![Deployed changeset in drawer](images/alfred-deployed.png)

## The Interface

When you open `/app/alfred-chat`, you see a single-column chat. The
preview lives in a drawer that slides in from the right when it has
something to show.

```
┌──────────────────────────────────────────────────────────────┐
│ [<-] [A] Title · [ModeSwitcher]   [≡] [+ New] [...]          │ <- frosted topbar (48px)
├──────────────────────────────────────────────────────────────┤
│                                                              │
│          ┌──── ● Ready  OR  [mark] Developer...┐             │ <- floating status pill
│          └─────────────────────────────────────┘             │
│                                                              │
│   [user msg]                                                 │
│   [agent msg]                                                │
│   [agent-step]                                               │
│                                                              │
│                                                              │
│        ┌────────────────────────────────────┐                │
│        │ Composer (centered, max 760px)     │  [Send ->]     │ <- floating composer
│        │ Enter to send, Shift+Enter newline │                │
│        └────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
                          ┌───────────────────────────────┐
                          │ Preview (slides in from right │
                          │  when a changeset arrives)    │
                          └───────────────────────────────┘
```

### Status pill (top of transcript)
- **Idle**: small green dot + **Ready**. Sits at ~75% opacity; brightens on hover.
- **Processing**: pulsing chat-gradient mark + bold agent name + live activity phrase (e.g. "Developer - generating code"). Click to expand a popover that shows the full six-step pipeline (Requirements -> Assessment -> Architecture -> Development -> Testing -> Deployment) with the current step highlighted.
- **Outcome**: briefly flashes green (**Completed**) or red (**Failed**) for ~4 seconds then returns to idle. The elapsed seconds counter sits to the right of the label during a run.
- **Basic mode**: the popover replaces the six-step pipeline with a "Basic mode" chip since the single-agent run has no visible phases.

### Preview drawer (right edge)
- **Auto-opens** when Alfred produces a changeset; the toolbar toggle (hamburger icon) shows a red dot until you see it.
- **Minimize** to a floating pill at the bottom-right showing the change count; click the pill to reopen at the exact scroll position.
- **Escape** closes the drawer (after the overflow menu and status popover, if those are open first).
- **Persisted** across reloads via localStorage. On mobile the drawer becomes a full-screen modal with a dimming scrim and focuses the close button for keyboard users.

### Stop a run

While a run is in flight the **Send** button is replaced with a **Stop** button. Clicking Stop sends a graceful cancel: the current agent phase completes, the pipeline exits cleanly, the conversation is marked **Cancelled**, and the chat shows a neutral "Run cancelled" system message. The WebSocket stays open so you can keep chatting in the same conversation. If the processing app is unreachable, the conversation is still marked Cancelled locally so the UI does not stay stuck on "In Progress".

### Transcript + drawer scroll independently

The transcript fills the page width and scrolls on its own. The preview
drawer slides in from the right when there is something to review; on
desktop the chat area shoves left by the drawer width so both stay
readable side-by-side, and on mobile (below ~768px wide) the drawer
becomes a full-screen modal with a dimming scrim.

### Refresh during a run

Refreshing the page at any point keeps everything you had on screen.
The chat transcript, the floating status pill, and the preview drawer
all rebuild from the server. What you see after a refresh:

- **Mid-run**: the status pill picks up where it was - pulsing mark +
  live agent name + last-known activity phrase. Click the pill to
  expand the full six-step pipeline popover.
- **Awaiting review**: the Pending changeset is re-rendered inside
  the drawer with its Approve / Reject / Request Changes buttons. The
  drawer auto-opens on reload so you see it without any extra clicks.
- **After deploy**: the deployed changeset is shown read-only in the
  drawer with a green "Deployed successfully" banner and a
  **Rollback** button (when rollback data is available).
- **After rollback**: a neutral "Deployment rolled back" banner
  replaces the Deployed banner inside the drawer.
- **After a deploy failure**: a red "Deploy failed - rolled back"
  banner lists the failed steps inside the drawer.
- **After Stop**: the conversation reads **Cancelled** with a neutral
  message in the pill's outcome flash; send a new prompt to continue
  in the same conversation.

### Conversation list (no-chat route)
- Frosted topbar at the top: brand mark + "Conversations" title, search input in the center, gradient **+ New** on the right. Typing in search filters the rows in place.
- Rows are grouped by time: **Today**, **Yesterday**, **Last 7 days**, **Last 30 days**, **Older**. Empty buckets are hidden.
- Each row is a single line: a colored mode dot, a live-run pulse (when a pipeline is currently running on that conversation), the first message as the title, then a meta zone on the right carrying:
  - A built-summary tag pulled from the latest changeset (e.g. "DocType: Book" for a single item, "3 changes" for multiples).
  - A changeset-state chip (**Pending approval**, **Approved**, **Deploying**, **Deployed**, **Rejected**, **Rolled back**). If the conversation has no changeset yet, a fallback chip appears only when the conversation status needs your attention (**Awaiting Input**, **Failed**, **Escalated**).
  - A "Shared" badge if the conversation was shared with you (not owned).
  - The pretty date plus message count (e.g. "2h ago · 12 msgs").
- Hover a row to reveal **Share** and **Delete** icons at the right edge (owner only). Clicking a row opens the chat shell (topbar + transcript + composer) in place of the list.

### Preview drawer
- Auto-opens when Alfred has something to show (during VALIDATING, DEPLOYING, PENDING review, DEPLOYED, ROLLED_BACK, FAILED, REJECTED, CANCELLED). Stays closed during early-phase EMPTY / WORKING states (the pill popover carries progress detail instead).
- During early phases the drawer's own hero shows what Alfred is doing ("Gathering Requirements...", "Designing Solution...") if you open it manually.
- Once a changeset is ready, shows DocType field tables, script code, and permission grids; action buttons (Approve / Request Changes / Reject) sit at the bottom.
- Can be minimized to a floating chip at the bottom-right and reopened any time.

---

## Your First Conversation

### Step 1: Start

Open `/app/alfred-chat`. If this is your first time, you'll see a welcome screen with a brand mark, a short one-line greeting, three example prompts, and a **Start a conversation** button. Click a starter to jump straight into that prompt, or click **Start a conversation** to type your own.

**Good prompts are specific:**
> "Create a DocType called Training Program with fields: program_name (Data, required), duration_days (Int), trainer (Link to Employee), and status (Select: Draft/Active/Completed)"

**Vague prompts trigger questions:**
> "I need something for HR training"
>
> Alfred will ask: "What specific aspect of HR training do you want to manage? Training programs, training requests, attendance tracking, or something else?"

**Conversational messages get conversational replies.** When the
three-mode orchestrator is enabled (`ALFRED_ORCHESTRATOR_ENABLED=1` on
the processing app), Alfred recognises greetings, thank-yous, and meta
questions and replies in kind without running the full SDLC pipeline:

> **You:** hi
>
> **Alfred:** Hi! I'm Alfred, your Frappe customization assistant. Tell me what you'd like to build, or ask about what's already on your site.

These conversational turns are fast (~5 seconds), don't consume agent
tokens, and never produce a changeset.

**You can also ask about what's already on your site.** If you ask a
read-only question, Alfred answers from your live site state using
read-only tools - no build, no approval, no changes:

> **You:** what DocTypes do I have in the HR module?
>
> **Alfred (Insights):** You have 18 DocTypes in the HR module: Employee, Leave Application, Attendance, Expense Claim, ... The Leave Application DocType is submittable and currently has 2 custom fields added on this site.

Insights-mode replies are markdown, usually a few sentences or a short
list, and complete in about 10-30 seconds. They're budget-capped at 5
tool calls per question so the assistant won't fan out into expensive
queries.

**You can ask for a plan before committing to a build.** If you want to
discuss the approach first, phrase it as a design question and Alfred
responds with a structured plan panel:

> **You:** how would we approach adding approval to Expense Claims?
>
> **Alfred (Plan panel):**
> Title: *Approval workflow for Expense Claims*
> Summary: Add a 2-step approval with manager, then finance.
> Steps:
>   1. Create Workflow 'Expense Claim Approval'
>   2. Create Notification for the approver
> Doctypes touched: Workflow, Notification
> Open questions: Who approves when the manager is absent?
> [Refine] [Approve and Build]

Click **Refine** to tweak the plan with another prompt, or **Approve &
Build** to promote it straight to Dev mode. Approved plans are injected
into the Developer agent's context as an explicit spec so the build
follows your plan exactly.

**You can also force a specific mode via the switcher** in the chat
header (Auto / Dev / Plan / Insights). Auto is the default and lets
Alfred decide; the other three force the mode for every prompt on that
conversation. Your pick is remembered per conversation so switching
away and back doesn't reset it. Use the forced modes when Alfred keeps
mis-routing - e.g. force Insights to explore your site without any
build risk, or force Dev if you want to skip the planning dance and
go straight to code.

Build requests ("add a priority field to Sales Order") still run the
full 6-agent pipeline. See
[how-it-works.md#chat-modes-and-the-orchestrator](how-it-works.md#chat-modes-and-the-orchestrator)
for the full details.

### Step 2: Alfred Gathers Requirements

The **Requirement Analyst** agent processes your request. You'll see:
- The floating status pill at the top flips from "Ready" to a pulsing chat-gradient mark + "Requirement Analyst" + "gathering requirements". Click the pill to expand the six-step pipeline popover.
- Bouncing dots (typing indicator) appear in the chat as the first agent reply streams in.

**If your request is clear**, the agent moves to the next phase automatically.

**If your request is ambiguous**, a question card appears:

```
┌─────────────────────────────────────────────────────┐
│ (?) What fields should the Training Program have?    │
│                                                     │
│     [Name & Duration]  [Full Details]  [Custom]     │
│                                                     │
│     Alfred is waiting for your response              │
└─────────────────────────────────────────────────────┘
```

Click an option button or type your own answer. The input box re-enables automatically when Alfred asks a question.

### Step 3: Feasibility Check

The **Feasibility Assessor** verifies:
- You have permission to create the requested customization types
- No naming conflicts with existing DocTypes
- No workflow conflicts

**If everything passes**, it moves to design.

**If permissions are missing**, you'll see an error:
> "You don't have permission for this operation. Contact your administrator."
>
> This means your Frappe role doesn't include the permissions needed (usually System Manager). Ask your site admin to add your role to Alfred Settings → Allowed Roles.

### Step 4: Design & Development

The **Solution Architect** designs the technical solution, then the **Frappe Developer** generates the actual code. The status pill text updates progressively:

- "Designing Solution..." -> "Generating Code..."
- Once complete, the preview drawer slides in with the full changeset.

### Step 5: Validation

The **QA Validator** (plus a dedicated **pre-preview dry-run** against your live site) checks everything:
- Python syntax in Server Scripts (`compile()` check)
- JavaScript syntax in Client Scripts
- Jinja syntax in Notification subjects and message templates
- Field types are valid Frappe types
- Naming conflicts don't exist
- Permission checks are present in scripts
- Deployment order is correct (dependencies first)
- **Dry-run insert with savepoint rollback** - every proposed document is actually inserted into your database in a transaction, then immediately rolled back. This catches errors that only surface at insert time (missing mandatory fields, unresolved Link targets, etc.) **without** leaving any trace in your data.

**If validation fails**, Alfred automatically asks the Developer to fix the issues and runs the dry-run again (once). If it still fails, the preview drawer shows the concrete issues and you can:
- Click **Deploy Anyway** (if you know the error is a false positive)
- Click **Request Changes** and tell Alfred what to fix
- Click **Reject** and start over

### Step 6: Review & Approve

The preview drawer shows the complete changeset:

**Validation banner** at the top:
- ✓ **Validated - ready to deploy** - dry-run passed, deploy is safe
- ⚠ **N validation issue(s) found - review before deploying** - shows a list of critical/warning issues, Approve button relabels to "Deploy Anyway"

**DocType preview** - shows module, naming rule, submittable/tree/single flags,
and a field table:
| Field | Type | Label | Options | Required |
|-------|------|-------|---------|----------|
| program_name | Data | Program Name | | Yes |
| duration_days | Int | Duration (Days) | | |
| trainer | Link | Trainer | Employee | |
| status | Select | Status | Draft\nActive\nCompleted | |

**Notification preview** - document type, event, channel, subject with Jinja
template rendering, recipient summary (field/role/cc), `enabled` flag, and
the full HTML message body.

**Custom Field preview** - target DocType, field name (as `code`), type,
label, options, default, insert_after, required flag, list-view visibility.

**Server Script preview** - reference DocType, script type, doctype event,
cron / api_method / event_frequency for scheduled scripts, disabled flag,
and the full Python source in a syntax-highlighted block.

**Workflow preview** - workflow name, target DocType, state field, active
flag, and **two additional tables**:

*States:*
| State | Doc Status | Allow Edit | Update Field |
|-------|-----------|-----------|-------------|
| Draft | Draft (0) | Employee, Leave Approver | |
| Pending Approval | Submitted (1) | Leave Approver | |
| Approved | Submitted (1) | | |

*Transitions:*
| From State | Action | To State | Allowed | Condition |
|-----------|--------|----------|---------|-----------|
| Draft | Submit | Pending Approval | Employee | |
| Pending Approval | Approve | Approved | Leave Approver | |

**Permission preview:**
| Role | Read | Write | Create | Delete |
|------|------|-------|--------|--------|
| System Manager | Yes | Yes | Yes | Yes |

Below the preview, a summary shows: "3 operation(s) will be applied to your site"

**Reflection banner** (if the minimality step is enabled and dropped anything):
you'll see a purple "Dropped N item(s) as not strictly needed" note listing
each trimmed item and why Alfred thought it wasn't in your original request.
Nothing you asked for is ever removed - only extras the agent volunteered.

Three buttons appear:

- **Approve & Deploy** - A confirmation dialog shows exactly what will be created. Click "Yes" to deploy.
- **Request Changes** - The input box focuses with placeholder "What would you like to change?" Type what you want different (e.g., "Add a description field and make status required").
- **Reject** - Cancels the changeset. You can start over with a new prompt.

### Step 7: Deployment

After you approve:
- The preview drawer shows a step-by-step progress tracker:
  ```
  ✓ Training Program (DocType)        Created
  ✓ validate_training (Server Script)  Created
  ⏳ Training Workflow                  In progress...
  ```
- Each step updates in real time
- On success: green banner "All changes deployed successfully"
- On failure: error message with automatic rollback, retry button available

### Step 8: After Deployment

Your customization is live. You can:
- Navigate to the new DocType (e.g., `/app/training-program`)
- **Continue the conversation with follow-up requests** - Alfred remembers
  every DocType, field, script, and notification it built earlier in this
  chat, plus the clarifications you provided. So after deploying a Training
  Program DocType you can say *"now add a description field to that DocType"*
  and Alfred will know "that" means Training Program without you spelling it
  out. The memory lasts for the lifetime of the conversation.
- Start a new conversation for a different customization (memory does not
  carry across conversations - each new chat is a fresh slate)

---

## Managing Conversations

### Conversation List

Your past conversations are grouped by time bucket (**Today**, **Yesterday**, **Last 7 days**, **Last 30 days**, **Older**) and filtered in place by the search input in the topbar. Each row shows:

- **Mode dot** - Colored circle on the left keyed to the conversation's mode (Auto / Dev / Plan / Insights).
- **Live pulse** - A small pulsing blue dot when a pipeline is currently running on the conversation.
- **Title** - The first message you sent (or the conversation name as fallback).
- **Built-summary tag** - What the latest changeset created or touched ("DocType: Book" for single-item, "3 changes" for multi-item). Hidden until a changeset exists.
- **Changeset-state chip** - Color-coded stage of the latest changeset (**Pending approval**, **Approved**, **Deploying**, **Deployed**, **Rejected**, **Rolled back**). Falls back to a conversation-status chip only when there is no changeset yet and the conversation needs your attention (**Awaiting Input**, **Failed**, **Escalated**).
- **Shared badge** - A small chip shown when the conversation was shared with you rather than owned by you.
- **Time + message count** - Pretty date of last activity with the message count inline (e.g. "2h ago · 12 msgs"). Singular at 1; suffix omitted at 0.
- **Hover actions** - Share and Delete icons fade in at the right edge on hover (owner only).

### Conversation Statuses

| Status | Color | Meaning | What to Do |
|--------|-------|---------|------------|
| Open | Blue | Created but no messages yet | Send your first prompt |
| In Progress | Orange | Agents are actively working | Wait - or answer if asked |
| Awaiting Input | Yellow | Alfred asked a question | Answer the question |
| Completed | Green | All done, changes deployed | Nothing - or ask a follow-up |
| Escalated | Red | Too complex for AI | A human developer will handle it |
| Failed | Red | Something went wrong | Read the error, retry or start over |
| Stale | Gray | Inactive for 24+ hours | Open and continue, or start a new one |
| Cancelled | Gray | You clicked Stop mid-run | Send a new prompt to continue in the same chat |

### Finding a Conversation

Conversations show the first message as a summary. If you have many, scroll through the list - they're sorted by most recent activity.

---

## When Things Go Wrong

### Error Messages

Alfred translates technical errors into plain language:

| You See | What It Means |
|---------|--------------|
| "There was a problem with the data format" | The generated code had a structural issue - Alfred will retry |
| "You don't have permission for this operation" | Your Frappe role can't create this type of customization |
| "A document with this name already exists" | The DocType or script name conflicts with something on your site |
| "The operation took too long" | LLM or processing timeout - try again |
| "Could not connect to the processing service" | The processing app is down - contact your admin |
| "Your message was flagged by the security filter" | Your prompt matched a security pattern - rephrase it |

Every error message has:
- A **human-readable explanation** at the top
- An expandable **"Technical details"** section for admins
- A **"Retry"** button that resends your last message

### Escalation

If Alfred can't handle your request (too complex, repeated failures, ambiguous after 3 clarification attempts), the conversation is **escalated**:

1. Status changes to "Escalated"
2. System Managers receive an in-app notification and email
3. A human developer reviews the conversation and either:
   - **Takes over** - completes the work manually
   - **Returns to Alfred** - with clarified requirements for the AI to retry

You'll see a message in the chat: "This request has been escalated to a human developer."

### Rollback

If you need to undo a deployment:

1. Go to the Alfred Changeset record (linked from the conversation)
2. Click **"Rollback"**
3. Alfred checks if any created DocTypes have user-entered data:
   - **No data** → DocType is deleted cleanly
   - **Has data** → Deletion is skipped to protect your data. A message tells you the record count and suggests manual cleanup.
4. Updated documents are restored to their original state
5. Changeset status changes to "Rolled Back"

---

## Tips for Better Results

### Be Specific About Fields
Instead of: "Create a task tracker"
Say: "Create a DocType called Task with fields: title (Data, required), description (Text), priority (Select: Low/Medium/High/Critical), assigned_to (Link to User), due_date (Date), and status (Select: Open/In Progress/Done)"

### Mention Relationships
If your DocType needs to link to existing DocTypes, say so:
"Create a Training Request DocType with employee (Link to Employee), training_program (Link to Training Program), and request_date (Date)"

### Specify Permissions
"Only HR Managers should be able to create and approve training requests. Employees should be able to read their own."

### Specify Workflows Explicitly
"Create a workflow for Training Request: Draft → Submitted (by Employee) → Approved (by HR Manager) → Completed"

### One Thing at a Time
Alfred works best with focused requests. Instead of "build a complete HR module", break it down:
1. "Create a Training Program DocType with..."
2. "Create a Training Request DocType that links to Training Program..."
3. "Add a workflow to Training Request with approval..."
4. "Create a notification when Training Request is approved..."

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Enter | Send message |
| Shift + Enter | New line in message |
| Click option button | Auto-fills and sends that option |

---

## Data & Privacy

- **What Alfred sees**: Your conversation messages and your site's DocType structure (field names, types, permissions). Never your actual document data.
- **Where it's processed**: Depends on your LLM configuration:
  - **Ollama** (self-hosted) - Everything stays on your server. Nothing leaves your network.
  - **Cloud providers** (Claude, GPT, Gemini) - Conversation messages are sent to the provider's API. Your site schema is included for context. No document data is sent.
- **What's stored**: Conversations, messages, changesets, and audit logs are stored in your Frappe site's database. They can be viewed at `/app/alfred-conversation`.
- **Audit trail**: Every action Alfred takes is logged in the Alfred Audit Log with before/after state snapshots.
