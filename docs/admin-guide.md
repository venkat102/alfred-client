# Alfred Admin Configuration Guide

## Part 1: Customer Site Configuration (Alfred Settings)

Access at `/app/alfred-settings`. Only System Managers can configure.

### Connection Tab

| Field | Description | Default |
|-------|-------------|---------|
| Processing App URL | WebSocket URL of the processing service | — |
| API Key | Shared secret for authentication (encrypted) | — |
| Self-Hosted Mode | Enable when running your own processing app | Off |
| Redis URL | Redis connection (self-hosted only) | — |

### LLM Configuration Tab

| Field | Description | Default |
|-------|-------------|---------|
| LLM Provider | ollama, anthropic, openai, gemini, bedrock | — |
| LLM Model | Model identifier (e.g., `ollama/llama3.1`) | — |
| LLM API Key | Provider API key (not needed for Ollama) | — |
| LLM Base URL | Custom endpoint (e.g., `http://localhost:11434`) | — |
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
| Default Plan | Auto-assigned plan for new customers | — |
| Warning Threshold % | Token usage percentage that triggers warning | 80 |
| Trial Duration Days | Default trial length | 14 |
| Service API Key | Authentication key for Processing App | — |

### Alfred Plan

Define subscription tiers:
- **Plan Name**: Display name (e.g., "Free", "Pro", "Enterprise")
- **Monthly Price**: Subscription cost
- **Monthly Token Limit**: Max tokens per month
- **Monthly Conversation Limit**: Max conversations per month
- **Max Users**: Concurrent users allowed
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
| `DEBUG` | No | Enable debug mode (default: false) |

---

## Troubleshooting

**"Processing App unreachable"**
- Check that the Processing App URL in Alfred Settings is correct (ws:// or wss://)
- Verify the Processing App is running: `curl http://processing-host:8000/health`
- Check firewall rules allow WebSocket connections

**"Permission denied" when using Alfred**
- Verify your role is in Alfred Settings > Allowed Roles
- If empty, only System Manager and Custom Field creators have access

**Agent keeps looping without progress**
- Max retries might be too high — reduce in Alfred Settings > Limits
- Check the LLM configuration — ensure the model is responsive
- Review the conversation for ambiguous requirements

**Deployment failed with rollback**
- Check the Alfred Changeset > Deployment Log for the specific error
- Common causes: naming conflict, permission revoked, DocType already exists

**Escalated conversations not receiving notifications**
- Verify email is configured in Frappe (Setup > Email Account)
- Check that System Managers have valid email addresses
