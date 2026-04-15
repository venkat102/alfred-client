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
| LLM Model | Model name (e.g., `codegemma:7b`, `llama3.1`). Auto-prefixed with provider. | - |
| LLM API Key | Provider API key. Optional for Ollama (needed if proxy requires auth). | - |
| LLM Base URL | Endpoint URL. Local Ollama: `http://localhost:11434`. Remote: `http://server-ip:11434`. Empty for cloud. | - |
| Max Tokens | Max tokens per LLM response | 4096 |
| Temperature | Generation randomness (0.0-2.0) | 0.1 |

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

> **Terminology note**: The codebase uses two orthogonal phase taxonomies. *Phase 1 / Phase 2 / Phase 3* refer to the architecture-improvement work tracked in `CHANGELOG.md` (tool hardening, handoff condenser, state machine, reflection, tracer). *Phase A / Phase B / Phase C / Phase D* refer to the three-mode chat rollout (chat handler + sanitizer fix → insights mode → plan mode + cross-mode handoff → UI mode switcher). All four phases are now shipped. The env var names above don't encode either — they describe what the flag does, not when it shipped.

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
