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
| Docker | 20+ (for processing app) | Latest |

### Software Prerequisites

- Frappe Bench (v15+) installed and working (`bench start` runs)
- A Frappe site created (e.g., `dev.alfred`)
- Redis running (Frappe's bench includes this)
- Docker + Docker Compose (for the processing app and Ollama)

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
│  /app/alfred-chat ← Chat UI           │
│  /app/alfred-settings ← Config   │
│  MCP Server (9 tools)            │
│  Deployment Engine               │
└──────────┬───────────────────────┘
           │ WebSocket (outbound)
           ▼
┌──────────────────────────────────┐
│  Processing App (Docker)         │
│  FastAPI + CrewAI agents         │
│  ┌────────┐  ┌────────┐          │
│  │ Redis  │  │ Ollama │          │
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

The processing app runs as a Docker container alongside Redis and Ollama (local LLM).

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
FALLBACK_LLM_MODEL=ollama/llama3.1
FALLBACK_LLM_BASE_URL=http://ollama:11434

# CORS - set to your Frappe site URL in production
# ALLOWED_ORIGINS=https://your-site.frappe.cloud
ALLOWED_ORIGINS=*

# Server
HOST=0.0.0.0
PORT=8001
WORKERS=2
DEBUG=false
```

> **Port conflict with Frappe**: Frappe's bench runs on port **8000** by default. Since the processing app also defaults to 8000, you **must** change one of them when both run on the same machine. We recommend setting the processing app to `PORT=8001` in `.env`. All examples in this guide use port 8001. Adjust if you chose a different port.

### 3. Start the Services

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

This downloads the AI model (~4.7 GB for llama3.1). Only needed once.

```bash
docker exec -it $(docker ps -qf "ancestor=ollama/ollama") ollama pull llama3.1
```

**For machines with less RAM** (< 12 GB), use a smaller model:
```bash
docker exec -it $(docker ps -qf "ancestor=ollama/ollama") ollama pull llama3.2:3b
```
Then set `FALLBACK_LLM_MODEL=ollama/llama3.2:3b` in `.env` and restart.

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
| LLM Model | `codegemma:7b` or `llama3.1` | Auto-prefixed to `ollama/codegemma:7b` on save |
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

### 2. Restart Frappe Workers

The WebSocket client runs as a background job. Restart workers to pick up the new config:

```bash
bench restart
```

---

## Part D: Verify the Installation

### Quick Health Checks

```bash
# 1. Processing app is running
curl http://localhost:8001/health
# Expected: {"status": "ok", "redis": "connected"}

# 2. Ollama has a model
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
   - Status bar shows the current agent and phase
   - Pipeline indicator highlights each step
   - Typing indicator (bouncing dots) appears while agents work
5. When the changeset preview appears in the right panel, review:
   - Field table showing the DocType fields
   - Permission matrix
6. Click **"Approve & Deploy"**
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
| Approve changes | Click "Approve & Deploy" in the preview panel |
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
| processing_app_url | Data | - | WebSocket URL of the processing app |
| api_key | Password | - | Shared secret for authentication |
| self_hosted_mode | Check | No | Enable for self-hosted deployments |
| redis_url | Data | - | Redis URL (self-hosted only) |
| llm_provider | Select | - | ollama, anthropic, openai, gemini, bedrock |
| llm_model | Data | - | Model ID (e.g., codegemma:7b, llama3.1). Auto-prefixed with provider on save. |
| llm_api_key | Password | - | API key for cloud LLM providers |
| llm_base_url | Data | - | Custom endpoint URL |
| llm_max_tokens | Int | 4096 | Max tokens per response |
| llm_temperature | Float | 0.1 | Generation randomness (0.0–2.0) |
| allowed_roles | Table | - | Roles permitted to use Alfred |
| enable_auto_deploy | Check | No | Skip manual approval |
| max_retries_per_agent | Int | 3 | Retries before escalation |
| max_tasks_per_user_per_hour | Int | 20 | Rate limit per user |
| task_timeout_seconds | Int | 300 | Per-agent timeout |
| stale_conversation_hours | Int | 24 | Hours before marking stale |

### Processing App Environment Variables

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
| ALLOWED_ORIGINS | No | * | CORS origins (comma-separated) |
| ADMIN_PORTAL_URL | No | - | Admin portal URL (SaaS mode) |
| ADMIN_SERVICE_KEY | No | - | Admin portal auth key |
| DEBUG | No | false | Enable debug logging |

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
# Processing app logs
docker logs -f $(docker ps -qf "name=processing")

# Ollama logs
docker logs -f $(docker ps -qf "name=ollama")

# Frappe logs
tail -f ~/frappe-bench/logs/worker.error.log
```

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
| LLM Model | `llama3.1` (auto-prefixed to `ollama/llama3.1`) |
| LLM API Key | *(leave empty)* |
| LLM Base URL | `http://localhost:11434` |

Processing app `.env`:
```env
FALLBACK_LLM_MODEL=ollama/llama3.1
FALLBACK_LLM_BASE_URL=http://localhost:11434
```

#### Remote Ollama (separate server, accessed via network)

If Ollama runs on a different machine (e.g., a GPU server), point the Base URL to that server's IP:

| Field | Value |
|-------|-------|
| LLM Provider | `ollama` |
| LLM Model | `codegemma:7b` (auto-prefixed to `ollama/codegemma:7b`) |
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

After configuring, you can verify the connection works:
```bash
# From the machine running the Processing App:
curl http://your-ollama-host:11434/api/tags
```

Or from Alfred Settings, the system validates connectivity when you save (checks if the endpoint is reachable and the model is installed).

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

**Pricing**: ~$3 per million tokens. A typical conversation uses 5,000–30,000 tokens (~$0.01–$0.09).

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
