# Alfred Developer & API Reference

## Processing App REST API

Base URL: `http://processing-host:8001`

### Health Check
```
GET /health
Response: {"status": "ok", "version": "0.1.0", "redis": "connected"}
```

### Create Task
```
POST /api/v1/tasks
Authorization: Bearer <api_key>

Body:
{
  "prompt": "Create a DocType called Book",
  "user_context": {"user": "admin@site.com", "roles": ["System Manager"]},
  "site_config": {"site_id": "site.frappe.cloud", "llm_model": "ollama/llama3.1", "max_tasks_per_user_per_hour": 20}
}

Response (201):
{"task_id": "uuid", "status": "queued"}
```

### Get Task Status
```
GET /api/v1/tasks/{task_id}?site_id=...
Authorization: Bearer <api_key>

Response: {"task_id": "...", "status": "running", "current_agent": "Architect"}
```

### Get Task Messages
```
GET /api/v1/tasks/{task_id}/messages?site_id=...&since_id=0
Authorization: Bearer <api_key>

Response: [{"id": "stream-id", "data": {...}}]
```

### Error Responses
```json
{"error": "message", "code": "ERROR_CODE"}

Codes: AUTH_MISSING, AUTH_INVALID, RATE_LIMIT, TASK_NOT_FOUND, REDIS_UNAVAILABLE,
       PROMPT_BLOCKED, NEEDS_REVIEW, PLAN_EXCEEDED, PIPELINE_TIMEOUT,
       PIPELINE_ERROR, PIPELINE_FAILED, EMPTY_CHANGESET
```

---

## WebSocket Protocol

### Connection
```
ws://processing-host:8001/ws/{conversation_id}
```

### Handshake (first message from client)
```json
{
  "api_key": "shared-secret",
  "jwt_token": "eyJ...",
  "site_config": {
    "llm_provider": "ollama",
    "llm_model": "ollama/llama3.1",
    "llm_max_tokens": 4096,
    "max_tasks_per_user_per_hour": 20,
    "pipeline_mode": "full"
  }
}
```

The JWT is signed with the shared API key and embeds `{user, roles, site_id,
iat, exp}`. The `user` field must be the **conversation owner** (not whoever
triggered `start_conversation`), because the processing app uses this identity
for every MCP tool dispatch during the session. `_connection_manager` enforces
this by loading the owner from `Alfred Conversation` before generating the JWT.

### Auth Success Response
```json
{"msg_id": "...", "type": "auth_success", "data": {"user": "...", "site_id": "...", "conversation_id": "..."}}
```

### Message Types (Server -> Client)
| Type | Description |
|------|------------|
| `auth_success` | Handshake accepted |
| `agent_status` | Phase / agent status update. Includes `phase`, `pipeline_mode`, `pipeline_mode_source`. |
| `agent_activity` | Live MCP tool call description (for the activity ticker) |
| `minimality_review` | Reflection step dropped one or more items from the changeset. Includes the dropped items + reasons. |
| `clarify` | Clarifier is asking a question or announcing done. |
| `validation` | Dry-run validation status update. |
| `question` | Agent asking user a question - expects `user_response` back. |
| `changeset` | Final changeset for approval. Includes `changes`, `dry_run`, `result_text`. |
| `mode_switch` | Orchestrator classified the prompt. Fires once per prompt when `ALFRED_ORCHESTRATOR_ENABLED=1`. Payload: `{conversation, mode, reason, source, confidence}` where `mode` is `dev`/`plan`/`insights`/`chat`, `source` is `override`/`fast_path`/`classifier`/`fallback`, `confidence` is `high`/`medium`/`low`. UI uses this for mode badges and auto-switch notices. |
| `chat_reply` | Conversational reply from the chat handler (no crew). Fires when the orchestrator routes to `chat` mode. Payload: `{conversation, reply, mode: "chat"}`. No approval needed, no changeset, no DB writes. |
| `insights_reply` | Markdown reply from the Insights handler. Fires when the orchestrator routes to `insights` mode. Payload: `{conversation, reply, mode: "insights"}`. Reply is markdown produced by a read-only single-agent crew that has access to `lookup_doctype`, `lookup_pattern`, `check_permission`, `get_existing_customizations`, and other read-only MCP tools. Tool budget is hard-capped at 5 calls per turn. No changeset, no DB writes. |
| `plan_doc` | Plan document from Plan mode (Phase C). Fires when the orchestrator routes to `plan` mode. Payload: `{conversation, plan, mode: "plan"}` where `plan` is a `PlanDoc`-shaped dict (`{title, summary, steps, doctypes_touched, risks, open_questions, estimated_items}`). Rendered as a structured panel (`PlanDocPanel.vue`) with Refine / Approve & Build buttons. Produced by a 3-agent crew (Requirement / Assessment / Architect) with a 15-call tool budget. No changeset, no DB writes. |
| `echo` | Echo response (for testing) |
| `error` | Error message with `code` field |
| `ping` | Heartbeat (every 30s) |
| `mcp_response` | MCP tool call result (routed to MCPClient for future resolution) |

### Message Types (Client -> Server)
| Type | Description |
|------|------------|
| `prompt` | User's text message. Optional `mode` field in `data` for the three-mode chat switcher (`"auto"`/`"dev"`/`"plan"`/`"insights"`, defaults to `"auto"`). |
| `user_response` | Answer to agent question |
| `deploy_command` | Deployment trigger |
| `ack` | Message acknowledgment |
| `resume` | Reconnection with last_msg_id |

### Message Envelope
```json
{"msg_id": "uuid", "type": "prompt", "data": {"text": "Create a Book DocType", "mode": "auto"}}
```

The `mode` field lets the UI force a specific chat mode, overriding the
orchestrator's automatic classification. When unset or `"auto"`, the
orchestrator decides. When set to `"dev"`/`"plan"`/`"insights"`, the
orchestrator skips the LLM classification call and uses the forced mode.
See [how-alfred-works.md#chat-modes-and-the-orchestrator](how-alfred-works.md#chat-modes-and-the-orchestrator)
for the full flow.

### MCP Dispatch (bidirectional over same WS)

The processing app sends MCP JSON-RPC 2.0 requests back to the client over the
same WebSocket while the crew is reasoning. The client dispatches them to its
local MCP server (`alfred_client.mcp.server.handle_mcp_request`) under the
connection manager's session user (the conversation owner).

```
Processing app → { "jsonrpc": "2.0", "method": "tools/call",
                   "params": {"name": "lookup_doctype", "arguments": {...}},
                   "id": <uuid> }

Client         → { "jsonrpc": "2.0", "id": <same uuid>,
                   "result": {"content": [{"type": "text", "text": "{...}"}]} }
```

Tool calls are dispatched synchronously on the `_listen_ws` loop so
`frappe.local.session.user` propagates correctly to permission checks. A
thread executor would lose the thread-local and run calls as Administrator.

---

## MCP Tools Reference (14 tools)

All tools callable via JSON-RPC 2.0 over WebSocket. Tool count + descriptions
must stay in sync with `alfred_client/mcp/tools.py::TOOL_REGISTRY` - adding or
renaming a tool requires updating `test_mcp.py` and this table.

### tools/list
```json
Request:  {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
Response: {"jsonrpc": "2.0", "id": 1, "result": {"tools": [...]}}
```

### tools/call
```json
Request:  {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_site_info"}, "id": 2}
Response: {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "..."}]}}
```

### Available Tools

| Tool | Tier | Args | Returns |
|------|------|------|---------|
| `get_site_info` | 1 | none | frappe_version, installed_apps, company, country |
| `get_doctypes` | 1 | module? | [{name, module}] |
| `get_doctype_schema` | 2 | doctype | fields, permissions, naming_rule (kept for backwards-compat; prefer `lookup_doctype`) |
| `get_existing_customizations` | 2 | none | custom_fields, server_scripts, client_scripts, workflows (site-wide summary) |
| `get_site_customization_detail` | 2 | doctype | deep per-DocType recon: workflows (full graphs), server_scripts (bodies truncated to 600 chars), custom_fields, notifications, client_scripts |
| `get_user_context` | 2 | none | user, roles, permitted_modules |
| `check_permission` | 3 | doctype, action | {permitted: bool} |
| `validate_name_available` | 3 | doctype, name | {available: bool} |
| `has_active_workflow` | 3 | doctype | {has_active_workflow: bool} |
| `check_has_records` | 3 | doctype | {has_records: bool, count: int} |
| `dry_run_changeset` | 3 | changes (list or JSON string) | {valid: bool, issues: [...], validated: [...]} |
| `lookup_doctype` | 1b | name, layer | Merged framework/site view of a doctype |
| `lookup_pattern` | 1b | query, kind | Curated customization pattern(s) |
| `lookup_frappe_knowledge` | 1c | query, kind?, k? | Frappe KB entries (rules / APIs / idioms / style) matching the query. Keyword search; the processing side adds semantic. |

### `lookup_doctype` details

Consolidated replacement for `get_doctypes` + `get_doctype_schema` + three
originally-planned Framework KG tools. Accepts a `layer` argument:

- `layer="framework"` - returns vanilla facts from `framework_kg.json`
  (extracted at `bench migrate` by walking every installed bench app's
  `doctype/*/*.json`). Use this when you need the mandatory field list for a
  `create` operation without pulling in whatever custom fields the user has
  added.
- `layer="site"` - live `frappe.get_meta()` including custom fields and
  site-level permission rows. Subject to row-level permission filters.
- `layer="both"` (default) - merged view:
  ```json
  {
    "name": "Sales Order",
    "framework": { ... vanilla schema ... },
    "site": { ... live schema ... },
    "custom_fields": [ ... fields present in site but not in framework ... ]
  }
  ```

`layer=framework` returns `{"error": "not_found"}` if the KG hasn't been built
(run `bench migrate` once to populate it). `layer=site` always hits live meta.
Invalid `layer` values return `{"error": "invalid_argument"}`.

### `lookup_pattern` details

Consolidated replacement for three originally-planned pattern-library tools.
Retrieves entries from `alfred_client/data/customization_patterns.yaml`:

- `kind="name"` - exact lookup by pattern name. Returns `{"pattern": {...},
  "name": "..."}` or `{"error": "not_found"}`.
- `kind="search"` - keyword search across names + descriptions + keywords.
  Returns `{"patterns": [...]}`.
- `kind="list"` - enumerate all registered patterns. Returns `{"patterns":
  [{name, description, when_to_use}, ...]}`.
- `kind="all"` (default) - try exact name first, fall back to search.

Each pattern carries a `when_to_use` / `when_not_to_use` / `template` /
`anti_patterns` section so the agent can pick the right idiom and adapt the
template to the user's target doctype. The 5 MVP patterns are:

- `approval_notification`, `post_approval_notification`,
  `validation_server_script`, `custom_field_on_existing_doctype`,
  `audit_log_server_script`.

### Insights mode tool subset (Phase B)

When the orchestrator routes a prompt to Insights mode, the single-agent
crew is bound to a **read-only subset** of the MCP tools. The subset is
returned by `build_mcp_tools(...)["insights"]` in
`alfred_processing/alfred/tools/mcp_tools.py` and covers exactly these
10 tools:

- `lookup_doctype` — primary DocType schema lookup (framework / site / both layers)
- `lookup_pattern` — curated customization pattern browser
- `get_site_info` — Frappe version, installed apps
- `get_doctypes` — browse DocTypes by module
- `get_existing_customizations` — custom fields, scripts, workflows on the site
- `get_user_context` — current user, roles, permitted modules
- `check_permission` — *"can I read/write this?"*
- `has_active_workflow` — workflow presence check
- `check_has_records` — does a DocType have data
- `validate_name_available` — name availability probe

**Excluded on purpose:**

- `dry_run_changeset` — deploy-shaped; Insights mode never produces
  changesets so the tool has no legitimate use.
- `get_doctype_schema` — deprecated in favor of `lookup_doctype`.
- `ask_user_stub`, `validate_python_syntax_stub`, `validate_js_syntax_stub`
  — local stubs meant for the dev-mode agents, not for read-only Q&A.

**Tool budget:** Insights runs under a tight `_INSIGHTS_TOOL_BUDGET = 5`
call cap (defined in `alfred/handlers/insights.py`). If the agent hits
the cap before finishing the answer, it surfaces what it found and
suggests the user rephrase. This is enforced by the Phase 1 per-run
MCP tracking state (`init_run_state(..., budget=5)`), the same
mechanism that caps dev-mode runs at 30 calls.

**Output shape:** Insights agents produce plain markdown (2-8 sentences
typically) and emit it through the WebSocket as an `insights_reply`
message. No JSON, no changeset. The frontend persists the reply as an
`Alfred Message` row with `metadata.mode = "insights"`.

### Plan mode crew + handoff (Phase C)

When the orchestrator classifies a prompt as `plan` (e.g. *"how would we
approach adding approval to Expense Claims?"*), the pipeline runs a
3-agent planning crew instead of a changeset crew. The crew is built by
`alfred_processing/alfred/agents/plan_crew.py::build_plan_crew` and
reuses the existing Requirement Analyst, Feasibility Assessor, and
Solution Architect agents (`alfred/agents/definitions.py::build_agents`).
The terminal task is named `generate_plan_doc` and must output a single
JSON object matching the `PlanDoc` Pydantic model in
`alfred/models/plan_doc.py`:

```json
{
  "title": "Approval workflow for Expense Claims",
  "summary": "Add a 2-step approval: manager, then finance.",
  "steps": [
    {"order": 1, "action": "Create Workflow 'Expense Claim Approval'",
     "rationale": "need approval", "doctype": "Workflow"},
    {"order": 2, "action": "Create Notification for approvers",
     "rationale": "notify them", "doctype": "Notification"}
  ],
  "doctypes_touched": ["Workflow", "Notification"],
  "risks": ["Submitted records would need to be re-submitted."],
  "open_questions": ["Who approves when manager is absent?"],
  "estimated_items": 2
}
```

**Tool budget:** Plan mode runs with `_PLAN_TOOL_BUDGET = 15` defined in
`alfred/handlers/plan.py` - more than Insights (5), less than Dev (30).
The 3 agents need enough calls to verify doctype schemas and look up
customization patterns, but not so many that they start wandering.

**Plan → Dev handoff:** When the user clicks "Approve & Build" in the
`PlanDocPanel`, the frontend sends the canned prompt *"Approve and
build the plan"* with `mode=dev`. The pipeline's `_phase_enhance` then:

1. Detects approval phrasing in `_maybe_approve_active_plan()` and
   flips `ConversationMemory.active_plan.status` from `proposed` to
   `approved`.
2. Calls `render_for_prompt()` which now emits the full step list
   (only approved plans render their steps verbatim - see
   `alfred/state/conversation_memory.py`).
3. Passes the rendered context block to `enhance_prompt()` so the
   downstream Dev crew sees the plan as an explicit spec.
4. After the Dev run completes, `_mark_active_plan_built_if_any()`
   flips the plan status to `built` so it isn't re-injected on the
   next Dev turn.

This handoff does not require a new API endpoint - it's all driven by
the normal prompt flow. Plan docs are persisted as `Alfred Message`
rows with `metadata.mode="plan"` and the full plan JSON in
`metadata.plan` so `PlanDocPanel` can reconstruct the plan on reload.

### Chat mode UI endpoints (Phase D)

Two new client-side whitelisted REST functions support the mode
switcher UI:

**`alfred_chat.set_conversation_mode(conversation, mode)`**

- Persists the user's chat mode preference on
  `Alfred Conversation.mode`. `mode` accepts `"Auto"`, `"Dev"`,
  `"Plan"`, `"Insights"` (case-insensitive, normalised to title case).
- Requires write permission on the conversation via
  `frappe.has_permission(..., ptype="write")`.
- Returns `{"name": conversation, "mode": normalised_value}`.

**`alfred_chat.send_message(conversation, message, mode="auto")`**

- `mode` is optional. When unset or `"auto"`, the orchestrator decides.
  When set to `"dev"` / `"plan"` / `"insights"`, the orchestrator
  respects the manual override and skips the LLM classifier.
- `mode` is included in the Redis prompt payload under `data.mode` so
  the processing app's WebSocket handler can thread it into
  `PipelineContext.manual_mode_override`.

### `dry_run_changeset` details

Validates a changeset **without** risking any write to the database. Routes
each item by doctype to one of two paths:

**DDL-triggering doctypes** (`DocType`, `Custom Field`, `Property Setter`,
`Workflow`, `Workflow State`, `Workflow Action Master`, `DocField`) go through
`_meta_check_only`, which:

1. Runs `frappe.get_doc(doc_data)` to catch unknown field names / bad child
   table shapes.
2. Runs a doctype-specific semantic check: Workflow states consistency,
   Custom Field target existence + conflict, DocType name collision, etc.
3. Walks `frappe.get_meta(doctype)` for mandatory fields.
4. Validates Link field targets exist on the live site.
5. **Never** calls `.insert()`, `.save()`, or `.validate()` - any of those can
   cascade into `ALTER TABLE` via controller hooks (e.g., Workflow's
   `on_update()` creates the `workflow_state` Custom Field). MariaDB
   implicitly commits on DDL, breaking savepoint rollback.

**Savepoint-safe doctypes** (`Notification`, `Server Script`, `Client Script`,
`Print Format`, `Letter Head`, `Report`, `Dashboard`, `Role`, `Translation`,
...) go through `_savepoint_dry_run`, which:

1. `frappe.db.savepoint("dry_run")`
2. `doc.insert(ignore_permissions=True)` or `.save()` for updates
3. `frappe.db.rollback(save_point="dry_run")` in a `finally`

Unknown doctypes fall through to `_meta_check_only` as the conservative default.

Regardless of path, runtime-error checks run on every item:

- **Server Script**: `compile()` check catches Python syntax errors.
- **Notification**: `frappe.render_template()` on subject/message/condition
  catches Jinja syntax errors (unterminated tags, invalid filters).
- **Client Script**: balanced-brace regex check.

Returns:

```json
{
  "valid": false,
  "issues": [
    {"step": 1, "severity": "critical", "issue": "...", "doctype": "...", "name": "..."}
  ],
  "validated": [
    {"step": 2, "doctype": "...", "name": "...", "operation": "create", "status": "ok"}
  ]
}
```

`valid` is `True` only when every issue has severity below `critical`. Used by
the pipeline's pre-preview dry-run and by `approve_changeset` as a second
safety-net check.

**Server Script import check.** `_check_runtime_errors` AST-walks every Server
Script body. `Import` / `ImportFrom` nodes trigger a critical issue with a
message that names the offending line and lists the pre-bound alternatives
(`json`, `datetime`, `frappe.*`, `frappe.utils.*`, `frappe.make_get_request`).
Runs before any savepoint insert, so imports are rejected at dry-run rather
than at runtime. `frappe.utils.safe_exec` exposes no `__import__`, so a plain
`compile()` wouldn't catch this.

### `lookup_frappe_knowledge` details

Third retrievable knowledge layer alongside `lookup_doctype` (schemas) and
`lookup_pattern` (recipes). Holds **platform rules**, **Frappe API
reference**, **idioms**, and **house style**. Source-of-truth YAMLs live at
`alfred_client/data/frappe_kb/`; loader + keyword search at
`alfred_client/mcp/frappe_kb.py`. The processing app layers semantic
retrieval on top via `alfred_processing/alfred/knowledge/fkb.py`.

```json
Args:
  query: str          required  free text, e.g. "server script import"
  kind:  str|None     optional  "rule" | "api" | "idiom" | "style" | None
  k:     int          optional  top-k hits, default 3

Returns:
  {"entries": [
     {"id": "server_script_no_imports", "kind": "rule",
      "title": "...", "summary": "...", "body": "...",
      "keywords": [...], "examples": {...},
      "_score": 18, "_mode": "search"}
  ], "mode": "search", "query": "..."}

Empty query -> discovery mode:
  {"entries": [{"id": "...", "title": "...", "summary": "..."}, ...],
   "mode": "list"}

Invalid kind -> {"error": "invalid_argument", "message": "..."}
```

Typical use: the pipeline's `inject_kb` phase auto-injects the top matches
into the Developer task, so agents don't need to call this manually. Agents
still can call it for depth on a specific topic. For semantic / hybrid
retrieval, use the processing-side `alfred.knowledge.fkb.search_hybrid`
directly - the MCP tool is keyword-only because the bench venv can't host
`sentence-transformers`.

### `get_site_customization_detail` details

Deep per-DocType recon. Where `get_existing_customizations` returns a
site-wide summary (names + event types), this returns full workflow graphs,
Server Script bodies, Custom Field details, Notifications, and Client
Scripts for ONE DocType.

```json
Args:
  doctype: str   required  target DocType name

Returns:
  {"doctype": "Employee",
   "custom_fields":  [{fieldname, fieldtype, label, options, reqd}, ...],
   "server_scripts": [{name, script_type, doctype_event, script,
                        disabled}, ...],                         # body <= 600 chars
   "workflows": [{name, is_active, workflow_state_field,
                   states:[{state, doc_status, allow_edit}, ...],
                   transitions:[{state, action, next_state, allowed}, ...]}],
   "notifications":  [{name, event, channel, subject, enabled}, ...],
   "client_scripts": [{name, view, script_preview, enabled}, ...]}   # preview <= 300

Errors:
  {"error": "not_found",    "message": "..."}  # DocType doesn't exist
  {"error": "permission_denied", "message": "..."}  # caller can't read it
  {"error": "invalid_argument",  "message": "..."}  # empty arg
```

Read-permission-gated on the parent DocType. Used by the pipeline's
`inject_kb` phase to render the SITE STATE banner that prevents agents from
proposing customizations that conflict with what's already installed.
Script bodies are truncated - for full bodies, agents load the named
Server Script doc directly.

### Rehydrate endpoint (Frappe REST)

`alfred_client.alfred_settings.page.alfred_chat.alfred_chat.get_conversation_state`
returns the live state of a conversation for UI rehydration after a refresh
mid-run. Without this the chat UI resets when the pipeline is still running
in the background.

```json
POST /api/method/.../get_conversation_state
Args:
  conversation: str  Alfred Conversation name

Returns:
  {"is_processing":     bool,        # true if status in {In Progress, Awaiting Input}
   "status":            str,         # conversation status string
   "active_agent":      str | null,  # e.g. "Developer"
   "active_phase":      str | null,  # e.g. "development" (derived from active_agent)
   "completed_phases":  [str],       # phases earlier than active in AGENT_PHASE_MAP
   "pending_changeset": dict | null} # latest Pending Alfred Changeset, if any
```

The chat UI calls this right after `get_messages` on `openConversation`.
Restores the preview panel, the active phase pill, and the processing flag
so a mid-run refresh doesn't drop to an idle screen.

---

## Pipeline State Machine

`alfred/api/pipeline.py::AgentPipeline` runs the pipeline as 11 named phases
over a shared `PipelineContext`:

```python
PHASES = [
    "sanitize",       # prompt defense - blocks injection-shaped prompts
    "load_state",     # Redis + conversation memory
    "plan_check",     # admin portal check_plan (optional)
    "orchestrate",    # three-mode chat: dev / plan / insights / chat
    "enhance",        # prompt enhancer LLM call
    "clarify",        # clarification gate (up to 3 questions)
    "inject_kb",      # hybrid FKB retrieval + site reconnaissance ->
                      # prepend banner to enhanced_prompt
    "resolve_mode",   # full vs lite pipeline selection
    "build_crew",     # build CrewAI crew + per-run MCP tracking state
    "run_crew",       # kickoff the crew
    "post_crew",      # extract, rescue, reflect, dry-run, preview
]
```

Each phase is an async method on `AgentPipeline` (e.g., `_phase_sanitize`).
The orchestrator iterates them in order, auto-wraps each in a tracer span,
and exits on `ctx.should_stop = True` or exception. Adding a new phase = two
edits: add the method, append to `PHASES`.

Unit tests mock the connection + Redis + admin-portal and exercise each phase
in isolation. See `tests/test_pipeline_state_machine.py`.

### `PipelineContext` key fields

| Field | Phase set | Purpose |
|---|---|---|
| `conn`, `conversation_id`, `prompt` | init | Constructor args |
| `store`, `conversation_memory`, `user_context` | load_state | Services + derived user context |
| `plan_pipeline_mode` | plan_check | Tier-locked mode from admin portal (if configured) |
| `enhanced_prompt`, `clarify_qa_pairs` | enhance, clarify | Pre-crew LLM outputs |
| `pipeline_mode`, `pipeline_mode_source` | resolve_mode | `"full"` or `"lite"` + which layer won |
| `crew`, `crew_state`, `custom_tools` | build_crew | CrewAI + MCP tool set |
| `crew_result`, `result_text` | run_crew | Crew kickoff return value |
| `changes`, `removed_by_reflection` | post_crew | Final changeset + reflection drops |
| `dry_run_result` | post_crew | MCP dry_run_changeset output |
| `should_stop`, `stop_signal` | any phase | Abort hook; emits error at end of run |

---

## Reflection Minimality (Phase 3 #13)

Feature-flagged post-crew step that drops items the user didn't ask for.

```
Developer changeset → reflect_minimality() → kept items → dry_run
                              │
                              ▼
                      one focused LLM call
                      (temperature=0, max=256 tokens)
                      returns {"remove": [int], "reasons": [str]}
```

### Enabling

```bash
export ALFRED_REFLECTION_ENABLED=1
```

Default is **off**. The flag lets you toggle without a code change for
benchmark runs, A/B tests, or site-specific policy. When off, the step is a
silent no-op - the changeset passes through unchanged.

### Safety rails

- Single-item changesets are skipped entirely - the reviewer would never flag
  the only item as overreach.
- If the reviewer flags **every** index, the safety net logs a warning and
  keeps everything. Almost always a signal that the reviewer misread the
  prompt.
- Any LLM failure or parse error returns the original changeset unchanged.
  Never blocks the pipeline on a reflection failure.
- Drops are surfaced to the UI via a `minimality_review` WebSocket event so
  the user sees exactly what was removed and why.

### Unit tests

`tests/test_reflection.py` covers the parser, LLM mock path, the all-flagged
safety net, and the no-op paths (flag off, empty changeset, single item).

---

## Handoff Condenser (Phase 2)

`alfred/agents/condenser.py` wires a `Task.callback` onto each upstream SDLC
task. The callback runs AFTER the task completes but BEFORE the next task's
context is aggregated, so rewriting `task_output.raw` in place propagates
correctly via CrewAI's `aggregate_raw_outputs_from_task_outputs`.

Condensation strategy is deterministic - no extra LLM call:

1. Strip markdown code fences.
2. Try to parse as JSON; if successful, re-emit compact (no indent).
3. Find the outermost balanced `{...}` or `[...]` substring and parse that.
4. Tail-truncate to 1500 chars as a final fallback.

Skipped task names: `generate_changeset`, `validate_changeset`,
`deploy_changeset`. The first is the changeset artifact `run_crew` extracts
for the UI; the others are terminal.

---

## Conversation Memory (Phase 2)

```python
from alfred.state.conversation_memory import (
    ConversationMemory,
    load_conversation_memory,
    save_conversation_memory,
)
```

Per-conversation state persisted in Redis under `conv-memory-<conversation_id>`
via the existing `StateStore.set_task_state`. Loaded at the start of every
`_phase_load_state` call. Updated in `_phase_clarify` (qa pairs) and
`_phase_post_crew` (changeset items + prompt), then saved before the changeset
message is sent.

### Bounded fields

- `items`: `[{op, doctype, name, on?}]`, capped at 20.
- `clarifications`: `[{q, a}]`, capped at 10.
- `recent_prompts`: `[str]`, capped at 5, each truncated to 200 chars.

### Injection

`render_for_prompt()` returns a short text block prepended to the prompt
enhancer's user message:

```
=== CONVERSATION CONTEXT (earlier in this chat) ===
Already discussed / built:
- create Notification "Alert on New Expense Claim" on Expense Claim
- create Custom Field "priority" on Sales Order
User decisions:
- Q: When should the notification fire?
  A: On submit, not on save
Recent prompts in this chat:
- Email the expense approver when a new expense claim is submitted
=== END CONTEXT ===
```

This is what lets "now add a description field to that DocType" resolve - the
enhancer LLM sees the prior Custom Field + the user's clarification and
generates a fully-qualified new prompt.

---

## Tracing (Phase 3 #14)

See `alfred/obs/tracer.py`. Minimal zero-dep tracer with async context-manager
API + parent/child nesting via `ContextVar`. Wraps each `AgentPipeline` phase
automatically.

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `ALFRED_TRACING_ENABLED` | off | Master switch (`1`/`true`/`yes` to enable) |
| `ALFRED_TRACE_PATH` | `./alfred_trace.jsonl` | JSONL output location |
| `ALFRED_TRACE_STDOUT` | off | Also emit human-readable summary to stderr |

### JSONL span format

```json
{
  "name": "pipeline.run_crew",
  "trace_id": "a1b2c3d4e5f60718",
  "span_id": "fedcba9876543210",
  "parent_id": "1122334455667788",
  "start": 1712947200.123,
  "end": 1712947460.789,
  "duration_s": 260.666,
  "status": "ok",
  "error": null,
  "attrs": {
    "pipeline_mode": "full",
    "tasks": 6,
    "status": "completed"
  },
  "events": [],
  "conversation_id": "conv-abc123"
}
```

### Analyzing a trace

```bash
# Total pipeline time per conversation
jq -s 'group_by(.conversation_id) | map({conv: .[0].conversation_id,
       total: map(.duration_s) | add})' alfred_trace.jsonl

# Which phase takes the longest
jq -s 'group_by(.name) | map({phase: .[0].name,
       avg_s: (map(.duration_s) | add / length)})' alfred_trace.jsonl

# Errors in the last hour
jq 'select(.status == "error")' alfred_trace.jsonl
```

### Swapping to OpenTelemetry later

The call-site API is `async with tracer.span("name", **attrs) as span:`. That
mirrors OTel's context manager, so switching to `opentelemetry-api` is a
one-file change in `alfred/obs/tracer.py`. Call sites don't change.

---

## Admin Portal API

Base URL: `http://admin-site:8000`
Auth: `Authorization: Bearer <service_api_key>`

### Report Usage
```
POST /api/method/alfred_admin.api.usage.report_usage
Body: {"site_id": "...", "tokens": 1500, "conversations": 1, "active_users": 3}
Response: {"status": "ok"}
```

### Check Plan
```
POST /api/method/alfred_admin.api.usage.check_plan
Body: {"site_id": "..."}
Response: {
  "allowed": true,
  "remaining_tokens": 85000,
  "remaining_conversations": 40,
  "tier": "Pro",
  "warning": null,
  "pipeline_mode": "full"
}
```

**`pipeline_mode`** (always present): returns `"full"` or `"lite"` based on the
`Alfred Plan.pipeline_mode` field. This is the tier-locked pipeline mode - the
processing app uses it to override the site's local `Alfred Settings.pipeline_mode`
for that conversation. Use this to lock starter plans to lite mode and unlock
full mode on higher tiers. Returns `null` when the customer has no plan, is
suspended, or doesn't exist.

Under admin override, the pipeline_mode is still read from the customer's
`current_plan` (if any) so an override-elevated customer stays on the tier
they paid for.

### Register Site
```
POST /api/method/alfred_admin.api.usage.register_site
Body: {"site_id": "...", "site_url": "https://...", "admin_email": "admin@..."}
Response: {"status": "created", "plan": "Free"}
```

### Subscribe to Plan (System-Manager only)
```
POST /api/method/alfred_admin.api.billing.subscribe_to_plan
Body: {"customer_name": "...", "plan_name": "Pro", "payment_reference": "..."}
Response: {"status": "subscribed", "subscription": "...", "plan": "Pro"}
```

Gated by `_require_billing_admin()` - an arbitrary logged-in portal user
cannot mutate subscriptions.

### Cancel Subscription (System-Manager only)
```
POST /api/method/alfred_admin.api.billing.cancel_subscription
Body: {"customer_name": "..."}
Response: {"status": "cancelled", "grace_period_ends": "2026-04-21"}
```

---

## Alfred Plan DocType

```
Plan Name (Data, unique) ──────────┐
Monthly Price (Currency)           │
Monthly Token Limit (Int, 100k)    │
Monthly Conversation Limit (Int, 50)│
Max Users (Int, 5)                 │
Pipeline Mode (Select, req'd)      │── check_plan() returns
    full | lite                    │   this value in response
Features (Table: Alfred Plan       │
    Feature)                       │
Is Active (Check, default 1) ──────┘
```

New plans default `pipeline_mode` to `full`. To tier-lock a starter plan to
lite, set the field to `lite` in the portal UI. The processing app's
`_phase_plan_check` reads it and overrides the customer site's local setting.

---

## Agent Output Schemas (reference only)

All agent outputs are defined as Pydantic models in `alfred/models/agent_outputs.py`.
They are **not** currently wired via CrewAI's `output_json` parameter - local
models (Ollama) wrap JSON in code fences that CrewAI's parser doesn't handle.
The models are still used by tests and are kept as authoritative schemas.

### RequirementSpec
```json
{"summary": "...", "customizations_needed": [...], "dependencies": [...], "open_questions": [...]}
```

### AssessmentResult
```json
{"verdict": "ai_can_handle|needs_human|partial", "permission_check": {"passed": bool, "failed": [...]}, "complexity": "low|medium|high", "risk_factors": [...]}
```

### ArchitectureBlueprint
```json
{"documents": [{"order": 1, "operation": "create", "doctype": "DocType", "name": "...", "design": {...}}], "deployment_order": [...], "rollback_safe": true}
```

### Changeset
```json
{"items": [{"operation": "create", "doctype": "DocType", "data": {...complete definition...}}]}
```

### TestReport
```json
{"status": "PASS|FAIL", "issues": [{"severity": "critical|warning", "item": "...", "issue": "...", "fix": "..."}], "summary": "..."}
```

### DeploymentResult
```json
{"plan": [...], "approval": "approved|rejected", "execution_log": [...], "rollback_data": [...], "documents_created": [...]}
```

---

## Adding New MCP Tools

1. Add the tool function in `alfred_client/mcp/tools.py`, decorated with
   `@_safe_execute` to catch and normalise exceptions to
   `{"error": "...", "message": "..."}`.
2. Register it in `TOOL_REGISTRY` at the bottom of the same file.
3. Add a CrewAI wrapper in `alfred_processing/alfred/tools/mcp_tools.py`.
4. Add to the appropriate agent's tool list in
   `alfred/agents/tool_stubs.py::TOOL_ASSIGNMENTS`.
5. Update the agent backstory if the new tool changes the agent's
   capabilities (keep it short - agents don't need tool tutorials).
6. Update `test_mcp.py::run_tests` to include the new name in
   `expected_tools` + a smoke-test for at least one call shape.
7. Update the tool table in this doc.

If the new tool overlaps with an existing one (same target data, different
args), prefer **consolidating** into one tool with a `layer`/`kind` argument
rather than adding a third entry. SWE-Agent ACI research showed that fewer
richer tools outperform many narrow ones for agent decision accuracy.

## Adding New Agents

1. Add backstory in `alfred/agents/backstories.py`.
2. Add agent creation in `alfred/agents/definitions.py::build_agents()`.
3. Add task in `alfred/agents/crew.py::TASK_DESCRIPTIONS` and include it in
   `build_alfred_crew()`'s task list.
4. Add output Pydantic model in `alfred/models/agent_outputs.py` even if not
   currently wired - it's the authoritative schema and tests reference it.
5. Add phase to the UI pipeline in `alfred.js::render_pipeline()` if the new
   agent should appear as a pipeline step.
6. Update the architecture.md diagram.

## Adding New Pipeline Phases

1. Add an `async def _phase_<name>(self):` method on `AgentPipeline` in
   `alfred/api/pipeline.py`. Read / mutate `self.ctx`; call `self.ctx.stop()`
   to abort with an error message.
2. Append `"<name>"` to `AgentPipeline.PHASES` at the position you want it
   to run.
3. The orchestrator auto-wraps the phase in a tracer span named
   `pipeline.<name>` - no manual span code needed.
4. Add a unit test in `tests/test_pipeline_state_machine.py` that stubs the
   phase and asserts on the shared context state.
