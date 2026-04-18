# Alfred Admin Configuration Guide

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
| `ALFRED_ORCHESTRATOR_ENABLED` | off | Enable the three-mode chat orchestrator. When on, every prompt is classified into `dev` / `plan` / `insights` / `chat`. Conversational prompts (greetings, thanks, meta questions) are short-circuited to a fast chat handler, read-only queries (*"what DocTypes do I have?"*) are routed to an Insights-mode single-agent crew with a 5-call read-only MCP tool budget, and build requests run the full 6-agent SDLC pipeline as before. `1` to enable. See `docs/how-alfred-works.md` for the full flow. |
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
