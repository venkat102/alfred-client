# Alfred — Complete Setup & Usage Guide

Everything you need to go from zero to a working Alfred installation. Follow these steps in order.

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
12. [Using Cloud LLM Providers](#using-cloud-llm-providers)
13. [Production Deployment](#production-deployment)
14. [Updating Alfred](#updating-alfred)
15. [Backup & Recovery](#backup--recovery)
16. [Monitoring](#monitoring)

---

## What is Alfred?

Alfred is an AI assistant that builds Frappe/ERPNext customizations through conversation. You tell it what you need in plain English — a new DocType, a workflow, a server script — and it designs, generates, validates, and deploys the solution to your Frappe site.

**Alfred consists of 3 components:**
- **Client App** (`alfred_client`) — A Frappe app installed on your site. Provides the chat UI, runs MCP tools for site context, and executes deployments.
- **Processing App** (`alfred_processing`) — A standalone FastAPI service that runs AI agents. Communicates with the client app via WebSocket.
- **Admin Portal** (`alfred_admin`) — Optional Frappe app for managing customers, plans, and billing (only needed if you're offering Alfred as a service).

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
│  /app/alfred ← Chat UI          │
│  /app/alfred-settings ← Config  │
│  MCP Server (9 tools)           │
│  Deployment Engine               │
└──────────┬───────────────────────┘
           │ WebSocket (outbound)
           ▼
┌──────────────────────────────────┐
│  Processing App (Docker)         │
│  FastAPI + CrewAI agents         │
│  ┌────────┐  ┌────────┐        │
│  │ Redis  │  │ Ollama │        │
│  └────────┘  └────────┘        │
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

# Get the app (if cloned from git)
bench get-app /path/to/alfred_client
# OR if it's already in apps/ directory:
bench install-app alfred_client --site dev.alfred
```

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

### 1. Navigate to the Processing App Directory

```bash
cd /path/to/alfred_processing
```

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

# CORS — set to your Frappe site URL in production
# ALLOWED_ORIGINS=https://your-site.frappe.cloud
ALLOWED_ORIGINS=*

# Server
HOST=0.0.0.0
PORT=8000
WORKERS=2
DEBUG=false
```

### 3. Start All Services

```bash
docker-compose -f docker-compose.selfhosted.yml up -d
```

This starts three containers:
- `processing` — FastAPI app on port 8000
- `redis` — State store on port 6379
- `ollama` — LLM server on port 11434

### 4. Pull the LLM Model

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
curl http://localhost:8000/health
```

Expected response:
```json
{"status": "ok", "version": "0.1.0", "redis": "connected"}
```

Also verify the API docs load: open `http://localhost:8000/docs` in your browser.

---

## Part C: Connect Them Together

### 1. Configure Alfred Settings

Open your Frappe site and navigate to: **`/app/alfred-settings`**

Fill in these fields:

#### Connection Tab
| Field | Value | Notes |
|-------|-------|-------|
| Processing App URL | `ws://localhost:8000` | Use your server IP if Frappe and Docker are on different machines |
| API Key | *(paste the `API_SECRET_KEY` from your `.env`)* | Must match exactly |
| Self-Hosted Mode | ✓ Check this | You're running your own processing app |

#### LLM Configuration Tab
| Field | Value |
|-------|-------|
| LLM Provider | `ollama` |
| LLM Model | `ollama/llama3.1` (or `ollama/llama3.2:3b` for smaller machines) |
| LLM Base URL | `http://localhost:11434` |
| Max Tokens | `4096` |
| Temperature | `0.1` |

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
curl http://localhost:8000/health
# Expected: {"status": "ok", "redis": "connected"}

# 2. Ollama has a model
curl http://localhost:11434/api/tags
# Expected: list containing "llama3.1" or your chosen model

# 3. Frappe site is running
curl http://dev.alfred:8000/api/method/frappe.client.get_count?doctype=Alfred+Settings
# Expected: {"message": 1}

# 4. Alfred page is accessible
# Open http://dev.alfred:8000/app/alfred in your browser
```

### First Conversation Test

1. Open `http://your-site:8000/app/alfred`
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
8. Verify: navigate to `/app/book` — you should see the new DocType

---

## Part E: Using Alfred

### What You Can Ask Alfred To Do

| Category | Example Prompt |
|----------|---------------|
| Create a DocType | "Create a DocType called Training Program with name, duration, and trainer fields" |
| Add fields to existing DocTypes | "Add a phone_number field to the Customer DocType" |
| Create a workflow | "Create an approval workflow for Leave Application with Draft, Pending Approval, and Approved states" |
| Create server scripts | "Create a validation that prevents submitting an expense claim over $10,000" |
| Create client scripts | "Add a filter on the Employee Link field in Leave Application to show only active employees" |
| Create notifications | "Notify the HR Manager when a new employee record is created" |
| Create reports | "Create a report showing all leave applications by department for the current month" |

### How the Chat Works

- **Type your request** in the input box and press Enter
- **Agent pipeline** runs automatically: Requirements → Assessment → Architecture → Development → Testing → Deployment
- **If Alfred asks a question**, answer it — the input box re-enables automatically
- **Review the preview** in the right panel before approving
- **Approve, Modify, or Reject** using the buttons in the preview panel

### What Alfred Cannot Do

- Modify core Frappe/ERPNext source code files
- Run bench commands or shell operations
- Access the file system
- Create features that don't exist in Frappe's customization framework
- Modify hooks.py or app-level Python files

### Conversation Status Guide

| Status | Meaning |
|--------|---------|
| Open | Conversation created, no messages yet |
| In Progress | Agents are working |
| Awaiting Input | Alfred asked a question, waiting for your answer |
| Completed | All done — changes deployed |
| Escalated | Too complex for AI — a human developer has been notified |
| Failed | Something went wrong — check the error message |
| Stale | Inactive for 24+ hours |

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
2. Set **Service API Key** — this authenticates the processing app
3. Set **Default Plan** — auto-assigned to new customers
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
| processing_app_url | Data | — | WebSocket URL of the processing app |
| api_key | Password | — | Shared secret for authentication |
| self_hosted_mode | Check | No | Enable for self-hosted deployments |
| redis_url | Data | — | Redis URL (self-hosted only) |
| llm_provider | Select | — | ollama, anthropic, openai, gemini, bedrock |
| llm_model | Data | — | Model ID (e.g., ollama/llama3.1) |
| llm_api_key | Password | — | API key for cloud LLM providers |
| llm_base_url | Data | — | Custom endpoint URL |
| llm_max_tokens | Int | 4096 | Max tokens per response |
| llm_temperature | Float | 0.1 | Generation randomness (0.0–2.0) |
| allowed_roles | Table | — | Roles permitted to use Alfred |
| enable_auto_deploy | Check | No | Skip manual approval |
| max_retries_per_agent | Int | 3 | Retries before escalation |
| max_tasks_per_user_per_hour | Int | 20 | Rate limit per user |
| task_timeout_seconds | Int | 300 | Per-agent timeout |
| stale_conversation_hours | Int | 24 | Hours before marking stale |

### Processing App Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| API_SECRET_KEY | Yes | — | JWT signing key + API auth key |
| REDIS_URL | Yes | redis://redis:6379/0 | Redis connection URL |
| HOST | No | 0.0.0.0 | Bind address |
| PORT | No | 8000 | Server port |
| WORKERS | No | 4 | Uvicorn worker count |
| FALLBACK_LLM_MODEL | No | — | Default LLM model |
| FALLBACK_LLM_API_KEY | No | — | Default LLM API key |
| FALLBACK_LLM_BASE_URL | No | — | Default LLM endpoint |
| ALLOWED_ORIGINS | No | * | CORS origins (comma-separated) |
| ADMIN_PORTAL_URL | No | — | Admin portal URL (SaaS mode) |
| ADMIN_SERVICE_KEY | No | — | Admin portal auth key |
| DEBUG | No | false | Enable debug logging |

---

## Troubleshooting

### "Processing App unreachable" when sending a message

**Cause**: The Frappe site can't reach the processing app via WebSocket.

**Fix**:
1. Verify the processing app is running: `curl http://localhost:8000/health`
2. Check the URL in Alfred Settings → Processing App URL
3. If Frappe and Docker are on different machines, use the server's IP instead of `localhost`
4. Check firewall allows port 8000
5. If behind nginx, add WebSocket proxy config (see below)

### "Not Authorized" when opening /app/alfred

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
3. Add GPU support (uncomment GPU section in docker-compose.selfhosted.yml)

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
    proxy_pass http://localhost:8000/ws/;
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

## Using Cloud LLM Providers

Ollama is free and private, but cloud providers give better results for complex tasks. Alfred supports any provider that LiteLLM supports.

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
| LLM API Key | *(leave empty — uses AWS credentials)* |
| LLM Base URL | *(leave empty)* |

### Switching Providers

You can switch providers at any time by updating Alfred Settings. Each new conversation uses the current configuration. Existing conversations keep the config they started with.

### Provider Comparison

| Provider | Speed | Quality | Cost | Privacy |
|----------|-------|---------|------|---------|
| Ollama (local) | Slow on CPU, fast with GPU | Good (llama3.1) | Free | Full — nothing leaves your server |
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
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://localhost:8000/ws/;
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
cloudflared tunnel --url http://localhost:8000
```

### Production Environment Variables

```env
# Strong secret — generate with: python3 -c "import secrets; print(secrets.token_urlsafe(64))"
API_SECRET_KEY=<64-char-random-string>

# Redis with password
REDIS_URL=redis://:your-redis-password@redis:6379/0

# CORS — restrict to your customer's sites
ALLOWED_ORIGINS=https://customer1.frappe.cloud,https://customer2.example.com

# Workers — 2 per CPU core
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
ExecStart=/opt/alfred_processing/.venv/bin/uvicorn alfred.main:app --host 0.0.0.0 --port 8000 --workers 4
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
docker-compose -f docker-compose.selfhosted.yml up -d --build

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
- **No permanent data is lost** — Redis is a cache, not the source of truth

---

## Monitoring

### Health Checks

Set up monitoring on these endpoints:

| Endpoint | Expected | Check Interval |
|----------|----------|---------------|
| `GET http://processing-app:8000/health` | `{"status": "ok", "redis": "connected"}` | 30 seconds |
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
