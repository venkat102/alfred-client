# Alfred Data Model Reference

Field-by-field reference for every Alfred DocType (client app + admin
portal) plus the Redis key conventions used by the processing app.

This doc is aimed at developers working on Alfred or writing reports
against Alfred's data. For the higher-level architecture, see
[architecture.md](architecture.md). For API shapes, see
[developer-api.md](developer-api.md).

---

## Client app (alfred_client)

Installed on every customer's Frappe site. DocTypes live in the
`Alfred Settings` module.

### Alfred Conversation

One record per chat session. Parent of messages, changesets, and audit
log entries.

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | autoname | yes | Short hash-style name (Frappe default) |
| `summary` | Data | | First message's text, truncated. Used as the card title in the conversation list. |
| `user` | Link(User) | **yes** | Conversation owner. Every permission check on child records delegates to this field. |
| `status` | Select | **yes** | `Open` / `In Progress` / `Awaiting Input` / `Completed` / `Escalated` / `Failed` / `Stale` |
| `current_agent` | Data | | Human-readable current phase ("Frappe Developer is working...") |
| `mode` | Select | | Three-mode chat (Phase D) sticky preference. `Auto` (default, orchestrator decides) / `Dev` / `Plan` / `Insights`. Written by `set_conversation_mode` when the user clicks a button in the `ModeSwitcher` Vue component. Read on conversation load to restore the header badge. |
| `requirement_summary` | Text | | Free-form summary from the requirement analyst |
| `escalation_reason` | Text | | Populated when `status = Escalated` |
| `token_usage` | JSON | | Running token counter per agent. Written by the processing app. |
| `created_documents` | Table(`Alfred Created Document`) | | Child table listing every document Alfred deployed during this session |

**Permissions**: owner + System Manager + users the owner shared with
via `frappe.share.add`. Enforced via
`alfred_client.api.permissions.conversation_has_permission` +
`conversation_query_conditions`.

### Alfred Created Document (child table)

Rows on `Alfred Conversation.created_documents`. Populated when
`apply_changeset` finishes a step.

| Field | Type | Notes |
|---|---|---|
| `document_type` | Data | E.g., `"Notification"`, `"Server Script"` |
| `document_name` | Data | E.g., `"Alert on Expense Claim"` |
| `operation` | Select | `Created` / `Modified` / `Deleted` |

### Alfred Message

Individual chat messages. Child of `Alfred Conversation`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `conversation` | Link(Alfred Conversation) | **yes** | Parent conversation |
| `role` | Select | **yes** | `user` / `agent` / `system` |
| `agent_name` | Data | | Which agent spoke (only when `role=agent`). For three-mode chat handlers: `"Alfred"` for chat mode, `"Insights"` for insights mode. |
| `message_type` | Select | | `text` / `question` / `preview` / `changeset` / `status` / `error` |
| `content` | Text Editor | | Message body. HTML for rich content. |
| `metadata` | JSON | | Typed payload (question choices, preview snapshot, error code, ...) depending on `message_type` |

**Permissions**: delegate to parent conversation.

**Three-mode chat handler replies (Phase A/B):** `chat_reply` and
`insights_reply` WebSocket events from the processing app are persisted
as `Alfred Message` rows by `alfred_client.api.websocket_client._store_agent_reply_message`.
They deliberately reuse `message_type="text"` (no schema migration) with
a `metadata.mode` marker (`"chat"` or `"insights"`) so the frontend's
`MessageBubble.vue` can render them with the correct mode badge. If you
query Alfred Message rows and need to distinguish handler replies from
regular dev-mode text, inspect `metadata.handler` (values: `"chat_reply"`
or `"insights_reply"`).

### Alfred Changeset

The proposed changes from one pipeline run. Shows in the preview panel.

| Field | Type | Required | Notes |
|---|---|---|---|
| `conversation` | Link(Alfred Conversation) | **yes** | Parent conversation |
| `status` | Select | **yes** | `Pending` / `Approved` / `Deploying` / `Rejected` / `Deployed` / `Failed` / `Rolled Back` |
| `dry_run_valid` | Check | | `1` if the pre-preview dry-run returned `valid=True` |
| `dry_run_issues` | Long Text | | JSON array of issues from the dry-run (empty when `dry_run_valid=1`) |
| `changes` | JSON | | The changeset itself - a JSON array of `{op, doctype, data}` items |
| `deployment_log` | Text | | JSON array of per-step deploy events (status, errors, timings) |
| `rollback_data` | JSON | | JSON array of inverse operations captured at deploy time. Used by `_execute_rollback`. |

**Status lifecycle**:

```
Pending ── (approve_changeset) ───▶ Approved ── (apply_changeset) ──▶ Deploying
   │                                    │                                 │
   │                                    │                                 ├──▶ Deployed
   ▼                                    ▼                                 │
Rejected                            Rejected                          Failed (rollback attempt)
                                                                          │
                                                                          ▼
                                                                    Rolled Back
```

The `Approved → Deploying` transition uses a distributed SQL lock
(`UPDATE ... WHERE status='Approved'`) to prevent concurrent deploys of
the same changeset.

**Permissions**: delegate to parent conversation.

### Alfred Audit Log

Write-ahead log of every Alfred-initiated change. Intended for
compliance - every deploy, modification, rollback, and error is
recorded with before/after state snapshots.

| Field | Type | Required | Notes |
|---|---|---|---|
| `conversation` | Link(Alfred Conversation) | **yes** | Parent conversation |
| `agent` | Data | | Which agent / role triggered the action |
| `action` | Data | **yes** | `deploy_create` / `deploy_update` / `rollback_delete` / `rollback_restore` / etc. |
| `document_type` | Data | | Target doctype |
| `document_name` | Data | | Target document name |
| `before_state` | JSON | | Snapshot of the document before the action (update/rollback only) |
| `after_state` | JSON | | Snapshot after the action (create/update only) |

Written BEFORE the actual operation so the log captures intent even if
the operation later fails.

**Permissions**: delegate to parent conversation (auto-readable if you
can read the conversation).

### Alfred Settings (singleton)

Site-level configuration. One row per site. Access at `/app/alfred-settings`.

| Tab | Field | Type | Notes |
|---|---|---|---|
| Connection | `processing_app_url` | Data | `ws://` or `wss://` URL of the processing app |
| | `api_key` | Password | Shared secret matching processing app's `API_SECRET_KEY` |
| | `self_hosted_mode` | Check | Marker for deployments that run their own processing app |
| | `redis_url` | Data | Self-hosted only; otherwise the processing app reads `REDIS_URL` from `.env` |
| | `pipeline_mode` | Select | `full` / `lite`. Local default. Overridden by admin portal's `check_plan.pipeline_mode` if configured. |
| LLM | `llm_provider` | Select | `ollama` / `anthropic` / `openai` / `gemini` / `bedrock` |
| | `llm_model` | Data | Model identifier (e.g., `llama3.1`, `claude-3-5-sonnet-20241022`) |
| | `llm_api_key` | Password | Provider API key (empty for Ollama unless proxy requires auth) |
| | `llm_base_url` | Data | Endpoint override. For remote Ollama: `http://server-ip:11434` |
| | `llm_max_tokens` | Int | Default 4096 |
| | `llm_temperature` | Float | Default 0.1 |
| | `llm_num_ctx` | Int | Ollama context window; 8192 typical |
| Access Control | `allowed_roles` | Table(`Alfred Allowed Role`) | Role allowlist. Empty falls back to System Manager. |
| | `enable_auto_deploy` | Check | **Off by default.** Skips the preview+approval step. Leave off in production. |
| Limits | `max_retries_per_agent` | Int | Default 3 |
| | `max_tasks_per_user_per_hour` | Int | Default 20 |
| | `task_timeout_seconds` | Int | Default 300 (per phase, not per pipeline) |
| | `stale_conversation_hours` | Int | Default 24 |
| Usage (read-only) | `total_tokens_used` | Int | Running counter written by the admin portal usage rollup |
| | `total_conversations` | Int | Running counter |

### Alfred Allowed Role (child table)

Rows on `Alfred Settings.allowed_roles`.

| Field | Type | Notes |
|---|---|---|
| `role` | Link(Role) | A Frappe role name |

---

## Admin portal (alfred_admin)

Installed on a dedicated admin site. DocTypes live in the
`Alfred Portal` module.

### Alfred Customer

One record per customer site that uses Alfred.

| Field | Type | Required | Notes |
|---|---|---|---|
| `site_id` | Data | **yes** | Canonical site identifier (e.g., `company.frappe.cloud`) |
| `site_url` | Data | | Full URL |
| `admin_email` | Data(Email) | **yes** | Contact for billing + notifications |
| `company_name` | Data | | Legal entity name |
| `current_plan` | Link(Alfred Plan) | | Which plan this customer is on. `None` means no active plan - `check_plan` will return `allowed: false, reason: "No plan assigned"`. |
| `status` | Select | | `Active` / `Suspended` / `Cancelled` |
| `override_limits` | Check | | Admin override for plan limits (temporary bypass) |
| `override_expiry` | Date | | Override valid through this date (inclusive). `None` means indefinite. |
| `trial_start` | Date | | |
| `trial_end` | Date | | |
| `total_tokens_used` | Int | | Running counter updated by `report_usage` |
| `total_conversations` | Int | | Running counter |

### Alfred Plan

Subscription tier definition.

| Field | Type | Required | Notes |
|---|---|---|---|
| `plan_name` | Data | **yes** | Unique. Display name (`Free`, `Pro`, `Enterprise`). |
| `monthly_price` | Currency | | Informational |
| `monthly_token_limit` | Int | | `0` / `None` = unlimited |
| `monthly_conversation_limit` | Int | | Same convention |
| `max_users` | Int | | Concurrent user seats |
| `pipeline_mode` | Select | **yes** | `full` / `lite`. **Tier-locked** pipeline mode returned to the processing app via `check_plan`. Use `lite` to lock starter plans, `full` to unlock the 6-agent crew on higher tiers. Default `full`. |
| `features` | Table(`Alfred Plan Feature`) | | Human-readable feature list |
| `is_active` | Check | | `1` to make the plan assignable |

### Alfred Plan Feature (child table)

Rows on `Alfred Plan.features`.

| Field | Type | Notes |
|---|---|---|
| `feature_name` | Data | E.g., `"Priority Support"` |
| `included` | Check | |

### Alfred Subscription

Billing lifecycle record. One active subscription per customer.

| Field | Type | Required | Notes |
|---|---|---|---|
| `customer` | Link(Alfred Customer) | **yes** | |
| `plan` | Link(Alfred Plan) | **yes** | |
| `status` | Select | | `Active` / `Trial` / `Past Due` / `Cancelled` / `Expired` |
| `start_date` | Date | **yes** | |
| `end_date` | Date | | Set at cancellation (start of grace period countdown) |
| `payment_reference` | Data | | Stripe / Razorpay reference |

**Lifecycle**: `Trial → Active → (Past Due) → Cancelled → Expired`.
Trial → Expired direct if no upgrade within `trial_duration_days`.

### Alfred Usage Log

Daily aggregate per customer. One row per `(customer, date)`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `customer` | Link(Alfred Customer) | **yes** | |
| `date` | Date | **yes** | |
| `tokens_used` | Int | | Cumulative over the day |
| `conversations` | Int | | Count completed that day |
| `active_users` | Int | | Max concurrent active users during the day |

Upserted by `alfred_admin.api.usage.report_usage`. The processing app
calls this after each pipeline run, passing the incremental tokens.

### Alfred Admin Settings (singleton)

Admin portal configuration.

| Field | Type | Notes |
|---|---|---|
| `grace_period_days` | Int | Days after cancel before subscription is marked Expired. Default 7. |
| `default_plan` | Link(Alfred Plan) | Auto-assigned to new customer registrations |
| `warning_threshold_percent` | Int | Token usage % that triggers a `warning` in `check_plan`. Default 80. |
| `trial_duration_days` | Int | Length of the free trial. Default 14. |
| `service_api_key` | Password | Bearer key for processing-app → admin-portal calls. Rotate separately from `API_SECRET_KEY`. |

---

## Conversation memory (processing app, Redis-backed)

`alfred.state.conversation_memory.ConversationMemory` is a dataclass
persisted per conversation. It's loaded at the start of every pipeline
run (in the `load_state` phase) and saved after changeset delivery,
Insights replies, and chat replies. The render method produces a
context block that's injected into the prompt enhancer and the
orchestrator classifier so follow-up turns can resolve references like
*"that DocType"* or *"build the plan we discussed"* deterministically.

| Field | Type | Cap | Purpose |
|---|---|---|---|
| `conversation_id` | str | - | Redis key and correlation id |
| `items` | list[dict] | 20 | Structured `(op, doctype, name, on)` tuples extracted from every changeset deployed in this conversation. Lets *"now add X to that DocType"* resolve. |
| `clarifications` | list[dict] | 10 | Q/A pairs from the clarification gate, so the user's stated constraints survive across turns. |
| `recent_prompts` | list[str] | 5 | Recent raw user prompts, capped at 200 chars each. Used by the orchestrator to give the classifier turn-level context. |
| `insights_queries` | list[dict] | 10 | Q/A pairs from Insights mode (Phase B). Each entry is `{"q": question, "a": answer_snippet}` where the answer is truncated to ~300 chars. Populated by `_run_insights_short_circuit` so later Plan/Dev turns can reference *"that workflow I asked about"*. |
| `plan_documents` | list[dict] | 5 | Full history of plans proposed in this conversation (Phase C). Each entry is a `PlanDoc`-shaped dict with an extra `status` key: `proposed` / `approved` / `built` / `rejected`. Older entries are trimmed when the cap is exceeded. |
| `active_plan` | dict or None | - | The most recently proposed or approved plan doc (Phase C). Set by `add_plan_document`, updated by `mark_active_plan_status`. When its status is `approved`, `render_for_prompt` emits the full step list so `_phase_enhance` can inject the plan into the Dev crew's input as an explicit spec. When status is `proposed` or `built`, only the summary is rendered. |
| `updated_at` | float | - | Unix timestamp of the last mutation |

Stored under Redis key `alfred:<site_id>:task:conv-memory-<conversation_id>`
via the existing `StateStore` task-state CRUD - no new key shape. The
schema migrates forward safely: `ConversationMemory.from_dict` tolerates
missing `insights_queries` / `active_plan` fields, so upgrading a site
doesn't invalidate existing memory blobs.

---

## Redis key conventions

The processing app stores transient state in Redis. All keys are
namespaced by site_id so multi-tenant deployments can share one Redis.

The Frappe side also stores queues in Redis (on a different port - see
[debugging.md](debugging.md#redis-debugging)).

### Processing app keys

| Key pattern | Lifetime | Purpose |
|---|---|---|
| `alfred:<site_id>:task:crew-state-<conversation_id>` | Per conversation, deleted on next prompt | CrewState for crash recovery (resume a pipeline mid-run). Cleared at the start of every new prompt so a fresh run doesn't inherit completed state from a prior turn. |
| `alfred:<site_id>:task:conv-memory-<conversation_id>` | Indefinite (no TTL) | `ConversationMemory` blob: items + clarifications + recent prompts + insights_queries + active_plan. See the "Conversation memory" section above for the field reference. Loaded at pipeline start, saved after dev-mode changeset delivery, chat replies, and insights replies. |
| `alfred:<site_id>:events:<conversation_id>` | Capped stream (default 1000 entries) | Event history for the REST polling fallback. Redis Stream. |
| `alfred:<site_id>:cache:<key>` | Per-key TTL | Generic TTL cache used by `StateStore.set_with_ttl` / `get_cached`. Not currently used by the pipeline but reserved. |

All set/get methods go through `alfred.state.store.StateStore` which
handles the namespace prefix and JSON (de)serialization.

### Frappe side keys (on `redis_cache`, port 13000)

| Key pattern | Purpose |
|---|---|
| `<db_prefix>\|alfred:ws:outbound:queue:<conversation_id>` | Durable message queue from Frappe workers → connection manager → processing app. `rpush` on write, `lpop` loop on read. |
| `<db_prefix>\|alfred:ws:outbound:<conversation_id>` (pub/sub) | `__notify__` / `__shutdown__` wakeup notifications (not data - messages come from the queue above) |
| `<db_prefix>\|<standard Frappe cache keys>` | Frappe's own cache |

`<db_prefix>` is Frappe's `RedisWrapper` auto-prefix. Pub/sub channels
are NOT prefixed.

### Frappe side keys (on `redis_queue`, port 11000)

| Key pattern | Purpose |
|---|---|
| `rq:queue:<site>:long` | RQ list for `long` queue jobs. `_connection_manager` is enqueued here. |
| `rq:queue:<site>:default` | Default RQ queue. |
| `rq:worker:...` | RQ worker heartbeats. |

---

## Cross-reference: which data lives where

| What | Where | Persistence |
|---|---|---|
| Chat messages | Frappe DB: `Alfred Message` | Forever (until user deletes) |
| Changesets | Frappe DB: `Alfred Changeset` | Forever |
| Audit log | Frappe DB: `Alfred Audit Log` | Forever |
| Conversation memory | Processing app Redis | Forever (no TTL, cleared only on delete) |
| In-flight crew state | Processing app Redis | Deleted on next prompt |
| Pipeline trace | Processing app filesystem: `alfred_trace.jsonl` | Forever (no rotation - manage yourself) |
| Per-run MCP tracking (budget, dedup) | In-memory on mcp_client instance | Lifetime of one pipeline run |
| Browser session | Frappe session cookie | Standard Frappe session expiry |
| WebSocket handshake JWT | Transport only | 24-hour expiry |
| Customer / Plan / Usage | Admin portal DB | Forever |
| LLM API keys | `Alfred Settings.llm_api_key` (encrypted Frappe password field) | Until rotated |
| `API_SECRET_KEY` | Processing app `.env` + `Alfred Settings.api_key` (encrypted) | Until rotated |

**What's backup-critical**: Frappe DBs (backup via `bench backup --with-files`
on both the customer site and admin portal). Everything else is
rebuildable or transient.

**What's secret-critical**: `.env` files + `Alfred Settings` password
fields + admin portal's `service_api_key`. Exclude from backups that
leave the host, store in a secret manager.
