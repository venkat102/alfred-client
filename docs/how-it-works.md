# How Alfred Works

This is the **single doc that explains Alfred from concept to code.** It's organized so you can read it linearly, OR jump to a section based on what you need:

- **30-second pitch** — what Alfred is + the three-process diagram
- **Glossary** — every Alfred-specific term, defined in one line each
- **Concrete data flow** — step-by-step file-by-file trace of a single user prompt
- **Narrative walk-through** (Part 1) — the user-facing story
- **Architecture reference** (Part 2) — canonical schema: phases, agents, state machine
- **Data model reference** (Part 3) — DocType fields + Redis keys + table relationships

If you're brand new, read top to bottom — ~30-45 minutes. Once you're working in the code, jump to the section you need and use the rest as reference.

---

## 30-Second Pitch

Alfred is an AI assistant that builds Frappe/ERPNext customizations through conversation. You describe what you need ("add a priority field to ToDo", "build a notification when an Invoice is overdue") and Alfred designs, generates, validates, and deploys it to your live Frappe site.

Under the hood, Alfred runs as **three processes** that talk to each other:

```
   ┌──────────────────┐         ┌────────────────────┐         ┌────────────────────┐
   │   Browser (Vue)  │ ◄─────► │  Frappe site       │ ◄─────► │  Processing App    │
   │  /alfred-chat    │  HTTP   │  alfred_client     │   WS    │  alfred_processing │
   │                  │  realtime│                    │   pubsub│  (FastAPI + crewAI)│
   └──────────────────┘         └────────────────────┘         └────────────────────┘
                                          ▲                              │
                                          │ MCP JSON-RPC                 │ LLM
                                          │ (tool calls back into        │ calls
                                          │  Frappe for live data)       ▼
                                          │                       ┌──────────────┐
                                          └───────────────────────│   Ollama /   │
                                                                  │   Anthropic  │
                                                                  └──────────────┘
```

- **Browser**: Vue chat UI (`alfred_client/public/js/alfred_chat/`). Sends prompts, renders agent activity, approves changesets.
- **Frappe site (`alfred_client`)**: a Frappe app. Owns the chat-page DocTypes (Alfred Conversation, Alfred Message, Alfred Changeset), the durable Redis queue between the browser and the Processing App, and the MCP server that the Processing App calls back into for live site data.
- **Processing App (`alfred_processing`)**: a separate FastAPI service. Runs the LLM-powered agents (CrewAI under the hood), the orchestrator that picks chat mode (dev / plan / insights / chat), and the dry-run validator. Talks to the Frappe site over a single WebSocket per conversation.

**Why three processes, not one?** The Frappe site is the customer's existing ERPNext install — touching it is sensitive, so the LLM-heavy Processing App lives separately and only ever talks back through a narrow JSON-RPC protocol (MCP, see glossary). This means a hosted Alfred service can run one Processing App that serves many customer Frappe sites, and a customer's Frappe site never has to install heavy AI dependencies.

---

## Glossary

Alfred-specific terms a Frappe-experienced developer would still need defined. One line each.

- **Changeset** — A JSON array of `{op, doctype, data}` items the agents produce as the deployable artifact. `op` is `create` or `update`. Stored in `Alfred Changeset.changes` as JSON. Validated by `dry_run_changeset`, deployed by `apply_changeset`.

- **Connection Manager** — A long-running RQ background job (`alfred_client.api.websocket_client._connection_manager`) on the Frappe side. One per active conversation. Holds a single WebSocket to the Processing App + drains the Redis durable queue. Survives the browser tab disconnecting.

- **Long Queue** — Frappe's `long` RQ queue. The connection manager runs there because the job lives for up to 6300 s (the `alfred_conn_max_lifetime` cap). The default `default` queue is for short jobs; running connection managers there would starve scheduled tasks.

- **MCP** (Model Context Protocol) — A JSON-RPC 2.0 sub-protocol carried over the same WebSocket as chat events. The Processing App's agents need live Frappe data ("does this DocType exist?", "do I have permission to write here?", "validate this changeset against the live DB"). MCP requests flow Processing App → Frappe; MCP responses come back. The 16 tools live in `alfred_client/mcp/tools.py:TOOL_REGISTRY`.

- **Orchestrator** — The mode-classifier in `alfred/orchestrator/mode.py`. On every prompt, it picks one of four modes: `dev` (build + deploy via 6-agent crew), `plan` (3-agent reviewable design doc), `insights` (read-only Q&A, 5-call MCP budget), or `chat` (conversational reply, no crew). Feature-flagged via `ALFRED_ORCHESTRATOR_ENABLED`.

- **The Crew** — The CrewAI sequence of agents that runs in dev mode. Six agents in Full mode: Requirement → Assessment → Architect → Developer → Tester → Deployer. One agent in Lite mode (the Developer alone). Backstories live in `alfred_processing/alfred/agents/backstories.py`.

- **Module Specialist** — A cross-cutting domain adviser (`alfred_processing/alfred/agents/specialists/module_specialist.py`) that injects ERPNext-module-specific conventions before the build phase and validates the output against module rules after. Ships as 13 module knowledge bases + 4 family KBs in `alfred/registry/modules/`. Feature-flagged via `ALFRED_MODULE_SPECIALISTS`.

- **Alfred Plan** — Two meanings, watch context:
  - The **plan-mode output**: a reviewable design doc produced by the 3-agent Plan crew (no DB writes). The user can click "Approve & Build" to promote it into a Dev run.
  - The **subscription tier DocType**: `Alfred Plan` is the admin-portal record that tier-locks features (which pipeline mode, which orchestrator flags, which LLMs are available).

- **Dry-Run** — The validation pass that runs before the user sees the changeset preview. `alfred_client.api.deploy.dry_run_changeset` walks every changeset item and either calls `_savepoint_dry_run` (DML-only doctypes — savepoint + rollback) or `_meta_check_only` (DDL-triggering doctypes — never call `.insert()`). Catches mandatory-field misses, link-target validity, fieldname conflicts.

- **The Savepoint Dance** — The two-track validation strategy in `dry_run_changeset`. For doctypes whose `.insert()` is pure DML (Notification, Server Script, etc.), Alfred opens a MariaDB savepoint, calls `.insert()`, then rolls back the savepoint — Frappe's full controller validation runs but the row is gone. For doctypes whose `.insert()` triggers DDL (DocType, Custom Field, Workflow — they `ALTER TABLE` or `CREATE TABLE`), savepoints DON'T work because MariaDB implicitly commits all pending DML before any DDL. Those go through a meta-only path that never calls `.insert()`. The split is enforced by `_DDL_TRIGGERING_DOCTYPES` and `_SAVEPOINT_SAFE_DOCTYPES` in `alfred_client/api/deploy/_constants.py`.

- **Run Evicted** — A sentinel event the Processing App pushes onto the conversation's Redis stream when a new WebSocket connects under the same `conversation_id` and the prior pipeline is being cancelled. The browser UI uses it as a splice point — events before it belong to the cancelled run, events after it belong to the live run.

- **`msg_id` Dedupe** — The client-side guard in `useAlfredRealtime.js` that drops realtime events the UI has already rendered. Catches two bug classes: (a) server resume-replay re-sending events past a stale `last_msg_id` cursor, (b) multi-tab broadcast (Frappe realtime is user-scoped, so every open tab sees every event).

- **`last_msg_id` Cursor** — A per-conversation cache key in `frappe.cache()` (`alfred:last_msg_id:<conv>`, 7-day TTL) that records the most recent event the browser rendered. The connection manager reads this on reconnect and asks the Processing App to replay events newer than the cursor. See `alfred_client/api/websocket_client/_cache.py`.

- **`_acting_as` Context Manager** — A safety helper in `alfred_client/api/deploy/_deployment.py` that wraps every changeset write in `with _acting_as(conversation_owner):`. It snapshots `frappe.session.user` + `session.sid` + `session.data`, calls `frappe.set_user(target)`, runs the wrapped block, and restores the snapshot. Without it, `frappe.set_user()` clobbers the caller's CSRF token + session record, and the next browser AJAX call bounces to login.

- **Disconnected-Session Queue** — A Redis LIST per conversation (`alfred:ws:outbound:queue:<conv>`) that buffers messages while the connection manager is reconnecting. Producers `rpush` to it; the manager drains via `lpop` on (re)connect. Capped at 10000 entries via `LTRIM` so a 24h+ disconnect can't grow it without bound.

- **Pipeline Phases** — The 12-phase state machine in `alfred_processing/alfred/api/pipeline/runner.py` (`PHASES` list). Every dev-mode run walks all 12 in sequence: `sanitize → load_state → warmup → plan_check → orchestrate → enhance → clarify → inject_kb → resolve_mode → build_crew → run_crew → post_crew`. Each phase is its own method on `AgentPipeline`; safety nets (drift / rescue / module-validation) run inside `post_crew`.

- **Per-Intent Builder Specialists** — Feature-flagged specialist agents (`ALFRED_PER_INTENT_BUILDERS`). When the orchestrator classifies the intent as `create_doctype` or `create_report`, the generic Developer is swapped for a domain-focused specialist (DocType Builder / Report Builder) carrying its own backstory + a registry-driven list of expected fields with rationales. Surfaces in the UI as editable "default" pills.

- **Multi-Module Classification** — Feature-flagged extension to module classification (`ALFRED_MULTI_MODULE`). Detects a primary module + up to two secondary modules for cross-domain prompts (e.g., "Sales Invoice that auto-creates a Project task" → primary=Accounts, secondary=[Projects]). Secondary-module blocker severities are capped to warning so only the primary can gate deploy.

- **Framework KG** — The Framework Knowledge Graph: pre-extracted metadata about every installed bench app's DocTypes (mandatory fields, link targets, field types). Built at `bench migrate` time, stored in `alfred_client/mcp/data/framework_kg.json`, queried via the `lookup_doctype` MCP tool. Authoritative answer to "what fields does Sales Invoice have?" without hitting the live meta cache.

- **Insights → Report Handoff** — Feature-flagged mode (`ALFRED_REPORT_HANDOFF`). When the user asks an analytics question in Insights mode and the query is "report-shaped" (tabular, filterable, aggregation-ready), Alfred attaches a "Save as Report" button to the answer. Clicking it fires a Dev-mode turn with `intent=create_report` already set — bypasses re-classification.

---

## Concrete Data Flow

Trace one user prompt from "click Send" to "changeset deployed". Each step names the file that owns it.

```
1. User clicks Send in /alfred-chat
        │
        ▼
2. Vue calls frappe.call("alfred_client.alfred_settings.page.alfred_chat.alfred_chat.send_message")
        │  Args: {conversation, message, mode}
        ▼
3. send_message() in
   alfred_client/alfred_settings/page/alfred_chat/alfred_chat.py:198
        │  - Permission check (alfred_role + write on Alfred Conversation)
        │  - Insert Alfred Message row (role=user)
        │  - Push prompt onto Redis durable queue + publish notify
        │  - Return {name, status, mode}
        ▼
4. Connection Manager (long RQ job) drains the queue
   alfred_client/api/websocket_client/_manager.py:_listen_redis
        │  - Reads via aioredis from the cache-Redis instance (port 13000)
        │  - Sends the prompt over the WebSocket to the Processing App
        ▼
5. Processing App receives the prompt over WS
   alfred_processing/alfred/api/websocket/connection.py:_listen_for_messages
        │  - Authenticated already at handshake (JWT verified)
        │  - Builds a PipelineContext (alfred/api/pipeline/context.py)
        │  - Starts AgentPipeline
        ▼
6. Pipeline runs 12 phases
   alfred_processing/alfred/api/pipeline/runner.py:AgentPipeline.run
        │  sanitize -> load_state -> warmup -> plan_check ->
        │  orchestrate (mode classifier) -> enhance -> clarify ->
        │  inject_kb -> resolve_mode -> build_crew -> run_crew -> post_crew
        ▼
7. Inside run_crew, the agents call MCP tools
   alfred_processing/alfred/tools/mcp_client.py
        │  e.g. dry_run_changeset, lookup_doctype, check_permission
        │  Sent as JSON-RPC requests over the SAME WebSocket
        ▼
8. Frappe side dispatches the MCP request
   alfred_client/api/websocket_client/_manager.py:_listen_ws
        │  Calls alfred_client.mcp.server.handle_mcp_request()
        │  Which routes to the matching @_safe_execute function in
        │  alfred_client/mcp/tools.py (TOOL_REGISTRY)
        ▼
9. MCP response sent back over WS, agent reasoning continues
        │
        ▼
10. Crew produces a changeset (JSON list of changes)
        │
        ▼
11. post_crew safety nets run:
    alfred_processing/alfred/api/safety_nets/{drift, rescue, backfill,
                                               report_handoff,
                                               module_validation,
                                               empty_changeset}.py
        │  Each one validates / repairs / annotates the changeset.
        ▼
12. Pipeline emits the final 'changeset' event over the WS
        │
        ▼
13. Connection manager routes the event back to the browser
    alfred_client/api/websocket_client/_routing.py:_route_incoming_message
        │  - Inserts/updates the Alfred Changeset row with the JSON
        │  - frappe.publish_realtime('alfred_preview', ...)
        │  - Caches msg_id + last_msg_id for resume
        ▼
14. Browser renders the preview panel
    alfred_client/public/js/alfred_chat/PreviewPanel.vue
        │  User reviews and clicks Approve.
        ▼
15. Vue calls alfred_client.api.deploy.apply_changeset
    alfred_client/api/deploy/_deployment.py:apply_changeset
        │  - Distributed-lock the changeset row (status -> Deploying)
        │  - with _acting_as(conversation_owner):
        │      for each change: dry-run again, then insert/update
        │      All inside a single Frappe transaction.
        │  - On any failure: rollback whole batch, set status Rolled Back
        │  - On success: commit, run verify_deployment, set status Deployed
        ▼
16. Verification + audit log written
        │
        ▼
17. Browser sees alfred_deploy_complete event, UI flips to "Deployed"
```

If you internalize one diagram, internalize this one. Every change you'll make to Alfred fits somewhere on this path.

---

# Part 1 — Narrative Walk-Through

How a single user prompt becomes a deployed change. Read this end-to-end at least once.


This document is the **cross-cutting explanation** of Alfred's architecture.
Every other doc in `docs/` is reference material organised by topic
(architecture, API, security, operations, data model). This one is a
**narrative tour**: it follows one concrete example prompt from the moment
a user types it to the moment Alfred's change is deployed and audited,
and at each step explains both what the user sees and what the code does.

Read this doc once, end-to-end, to get a real mental model of the system.
Read the other docs when you need specific details.

> **Not sure where to start?** See [../README.md](../README.md)
> for the recommended sequence through every Alfred doc with time
> estimates and self-tests.

> **Audience**: new engineers onboarding, security reviewers, technical
> decision-makers evaluating Alfred, anyone writing a presentation about
> it.
>
> **Prerequisites**: basic familiarity with Frappe (what a DocType is) and
> with async Python / WebSocket protocols. You don't need to know CrewAI
> or MCP - this doc explains them as they come up.
>
> **Estimated reading time**: 25-30 minutes.

---

## Table of contents

1. [The 30-second pitch](#the-30-second-pitch)
2. [Components and responsibilities](#components-and-responsibilities)
3. [Chat modes and the orchestrator](#chat-modes-and-the-orchestrator)
4. [Example prompt, end to end](#example-prompt-end-to-end)
5. [What happens in the Developer agent's head](#what-happens-in-the-developer-agents-head)
6. [Approval and deployment](#approval-and-deployment)
7. [A multi-turn follow-up](#a-multi-turn-follow-up)
8. [When things go wrong](#when-things-go-wrong)
9. [Rollback](#rollback)
10. [How the safety layers stack up](#how-the-safety-layers-stack-up)
11. [Why the design looks like this](#why-the-design-looks-like-this)
12. [Where to go next](#where-to-go-next)

---

## The 30-second pitch

Alfred turns natural-language requests into deployed Frappe customizations.
You type *"Email the expense approver when a new expense claim is
submitted"* into a chat UI on your Frappe site, and Alfred:

1. Rewrites the request into a concrete spec.
2. Asks you for any blocking clarifications (*"which field holds the
   approver's user?"*).
3. Runs a team of AI agents that design, code, validate, and plan the
   deployment.
4. Shows you a rich preview of every document that would be created -
   the full notification subject, the Jinja template, the trigger event,
   the recipients.
5. Deploys it with your approval, runs a second dry-run for safety, and
   logs every step for rollback.

All the AI work happens in a separate process (the **processing app**)
that talks to your Frappe site over a WebSocket. The agents query your
live site (schemas, permissions, existing customizations) through that
WebSocket using a protocol called **MCP** - so they're always reasoning
against the real shape of your system, not a snapshot from training data.

The result: a user who doesn't know the Frappe API can still get a
production-ready Notification document in ~5 minutes, with safety rails
(dry-run validation, preview + approval, audit log, rollback) at every
step.

---

## Components and responsibilities

Three processes, three trust boundaries.

```
┌────────────────────────────────┐
│  Browser                       │
│  - Vue chat UI                 │
│  - Frappe session cookie       │
└──────────────┬─────────────────┘
               │
               │ Socket.IO (to Frappe) +
               │ API calls to whitelisted methods
               ▼
┌────────────────────────────────┐
│  Frappe site                   │
│  (alfred_client app)           │
│                                │
│  - Chat UI backend             │
│  - Persistence: Alfred         │
│    Conversation / Message /    │
│    Changeset / Audit Log       │
│  - MCP server (14 tools) that  │
│    reads the live site        │
│  - Deployment engine           │
│  - Connection manager:         │
│    long-running RQ job per    │
│    active conversation, holds │
│    the WebSocket to the       │
│    processing app             │
└──────────────┬─────────────────┘
               │
               │ WebSocket (outbound, client-initiated)
               │   - API key + JWT handshake
               │   - Bidirectional: custom protocol +
               │     MCP JSON-RPC over the same socket
               ▼
┌────────────────────────────────┐
│  Processing app                │
│  (alfred_processing)           │
│                                │
│  - FastAPI WebSocket server    │
│  - AgentPipeline state machine │
│    (12 phases, tracer spans)   │
│  - CrewAI crew orchestrator    │
│    (full: 6 agents / lite: 1)  │
│  - MCP client that dispatches  │
│    tool calls back over the WS │
│  - Redis: conversation memory, │
│    crew state, event streams   │
└──────────────┬─────────────────┘
               │
               │ HTTPS (optional, SaaS only)
               ▼
┌────────────────────────────────┐
│  Admin portal                  │
│  (alfred_admin, separate site) │
│                                │
│  - Customer / Plan /           │
│    Subscription / Usage Log    │
│  - check_plan API: tier-locked │
│    pipeline_mode, usage gates  │
│  - Billing + trial lifecycle   │
└────────────────────────────────┘
```

**Why three processes?** Each has a different trust model and a different
lifecycle:

- The **Frappe site** is where your actual customizations live. It owns
  the data. Running AI agents directly inside Frappe would mean pip-installing
  CrewAI, LiteLLM, ChromaDB, ONNX Runtime, and their transitive deps into
  every customer's bench - a maintenance nightmare. Splitting the AI work
  out keeps Frappe lean.
- The **processing app** is stateless, GPU-adjacent, and has heavy Python
  deps. You can run one processing app per customer, or one shared
  processing app for many customers, or scale it horizontally behind a
  load balancer - all without touching the customer sites.
- The **admin portal** is optional (only for SaaS deployments) and
  exists so one team can manage quotas, plans, and billing across many
  customer sites.

Each boundary authenticates separately. Losing one credential doesn't
compromise the others. See [SECURITY.md](SECURITY.md#trust-boundaries) for
the full model.

---

## Three kinds of knowledge

Alfred keeps three separate knowledge stores, each authoritative for a
different kind of fact. None of them duplicate content, and none of them
live in agent backstories any more.

**Framework KG** (`alfred_client/data/framework_kg.json`) answers *what does
this DocType look like*. Auto-extracted from every installed bench app's
DocType JSONs at `bench migrate` - field list, permissions, naming rules.
Agents query via `lookup_doctype(name, layer="framework|site|both")`.

**Pattern library** (`alfred_client/data/customization_patterns.yaml`) answers
*what does a canonical <idiom> look like*. Hand-written recipes for common
customization shapes: approval_notification, post_approval_notification,
validation_server_script, custom_field_on_existing_doctype,
audit_log_server_script, create_role_with_permissions. Each has
when_to_use / when_not_to_use / template / anti_patterns. Agents query
via `lookup_pattern(name, kind)`. Multi-item templates (e.g.
`create_role_with_permissions` emits a Role plus one Custom DocPerm per
target DocType) are supported.

**Frappe Knowledge Base (FKB)** (`alfred_client/data/frappe_kb/*.yaml`) answers
*what are the rules that constrain what I generate*. Four kinds:

- `rules.yaml` - platform sandbox constraints. "Server Scripts cannot use
  `import`", "Workflow requires at least 2 states and a transition", "DocType
  names are Title Case, fieldnames are snake_case".
- `apis.yaml` - Frappe API reference. Auto-scraped signatures + docstrings
  for the `frappe.utils` whitelist, `frappe.db.*`, and the top-level
  `frappe.*` helpers agents actually reach for. Hand-overrides for the
  top 20 with real examples and pitfalls.
- `idioms.yaml` - "how Frappe wants it done". `hooks.py doc_events` wiring,
  submit/cancel lifecycle, rename flows, `frappe.enqueue`, assignment rules.
- `style.yaml` - Alfred's own code-gen preferences. Tabs, permission-check-
  first, `frappe.throw(_())` instead of raise, `"Alfred"` module default.

The FKB is retrieved via hybrid search (weighted keyword first, embedding-
based semantic as a rescue for phrasings keyword misses). Retrieval lives
in `alfred_processing/alfred/knowledge/fkb.py` so the sentence-transformers
dependency stays out of the bench venv.

**Auto-injection** is what ties it together. A pipeline phase called
`inject_kb` runs between `clarify` and `resolve_mode` on every Dev-mode
turn. It:

1. Runs hybrid search over the FKB on the clarified prompt and picks the
   top 3 entries.
2. Extracts the target DocType(s) from the prompt (up to 2) and calls
   `get_site_customization_detail(doctype)` to fetch the live site's
   existing customizations for each target - workflows, server scripts,
   custom fields, notifications.
3. Renders both into a banner prepended to the Developer task:

```
=== FRAPPE KB CONTEXT (auto-injected, reference only) ===
[top platform rules / APIs / idioms / style entries relevant to the request]
==========================================================

=== SITE STATE FOR "Employee" (already on this site) ===
Workflow: Employee Approval (active)
  states: Draft -> Submitted -> Approved
Server Script: Validate Join Date (Before Save, enabled)
  body preview:
    if not doc.date_of_joining:
        frappe.throw(_("Required"))
Custom Fields:
  - employee_code (Data, required)
=========================================================

--- USER REQUEST (interpret this verbatim) ---
[enhanced + clarified user request]
```

The agent doesn't have to know to call the retrieval tools - the context is
already in front of it. It still can call `lookup_frappe_knowledge(query)`
or `get_site_customization_detail(doctype)` for additional depth on
something specific.

Why this design instead of pasting the rules into agent prompts: we did
that for a while, and every new rule meant editing five files (both crew
task descriptions, all four agent backstories, the prompt enhancer) and
keeping them in sync forever. Now each rule lives in one YAML entry and
gets auto-injected when relevant.

`ctx.injected_kb` (list of entry IDs) and `ctx.injected_site_state` (dict
keyed by DocType) are logged to the tracer on every turn, so when a
generated changeset still gets a rule wrong, you can tell whether the rule
was injected and ignored or never injected in the first place.

---

## Chat modes and the orchestrator

Not every chat message is a build request. Users say "hi", ask "what
DocTypes do I have?", request a plan before committing to a build, or
just want a recap of what was done earlier. Running the full 6-agent SDLC
crew for every single prompt would be wasteful for these turns and
produce nonsense replies for conversational ones.

Alfred solves this with a small orchestrator phase that classifies each
prompt into one of four modes and routes it appropriately:

| Mode | What it does | Cost | When it runs |
|------|--------------|------|---------------|
| **Dev** | Current behavior. 6-agent crew (or 1-agent lite) produces a deployable changeset, runs dry-run, shows a preview, waits for approval, deploys. This is the only mode that writes to your DB. | Full pipeline: 3-10 min, 10k-30k tokens | Build/modify requests: "add a priority field", "create a DocType", "approve and deploy" |
| **Plan** | 3-agent planning crew (Requirement, Assessment, Architect). Produces a structured plan document (title, summary, numbered steps, doctypes touched, risks, open questions) rendered as a rich panel in the chat. No DB writes. The user clicks "Refine" to iterate or "Approve & Build" to promote the plan to Dev mode - the next turn then runs Dev with the plan JSON injected as a CONTEXT block so the Developer agent executes the plan verbatim. Hard-capped at 15 tool calls per turn. | ~1-2 min, ~3k-8k tokens | Design questions: "how would we approach adding approval to Expense Claims?" |
| **Insights** | Single-agent CrewAI crew with read-only MCP tools bound (`lookup_doctype`, `lookup_pattern`, `check_permission`, `get_existing_customizations`, `get_site_info`, `get_doctypes`, `has_active_workflow`, `check_has_records`, `validate_name_available`, `get_user_context`). Answers questions about the user's current site state in markdown. Hard-capped at 5 tool calls per turn. `dry_run_changeset` is explicitly excluded. No DB writes. | ~10-30s, ~1k-3k tokens | Read-only queries: "what DocTypes do I have?", "show me my workflows" |
| **Chat** | Single LLM call with conversation memory injected as context, no tools. Pure conversational reply. Handles greetings, thanks, meta questions about Alfred, and recap requests. | ~5-10s, <1k tokens | Conversational turns: "hi", "thanks", "what can you do?", "summarize what we built" |

### How the orchestrator decides

The orchestrator runs right after `load_state` and `plan_check`, before
`enhance`. It has four decision layers applied in priority order:

1. **Manual override** — if the user picked a specific mode in the chat
   UI header switcher (Phase D), that mode wins and no LLM call is
   made. The switcher persists per conversation via
   `Alfred Conversation.mode` so the preference survives page reload.
   `auto` (the default) falls through to step 2.
2. **Fast-path match** — deterministic rules that avoid an LLM call for
   obvious cases:
   - Exact greeting (`hi`, `thanks`, `ok`, `bye`, ...) → chat
   - Imperative build verb (`add a`, `create a`, `build a`, ...) → dev
   - Empty or whitespace-only prompt → chat
3. **LLM classification** — if fast-path didn't match, one short litellm
   call (128 max tokens, temperature 0) returns
   `{"mode": "...", "reason": "...", "confidence": "high|medium|low"}`.
   The classifier sees the prompt plus the last few turns from conversation
   memory so it can resolve references like "it" / "that".
4. **Confidence-based fallback** — if the classifier fails or returns low
   confidence, the orchestrator picks the safest default:
   - If the conversation has an approved plan in memory, fall back to
     **dev** (the user is probably continuing planned work).
   - Otherwise fall back to **chat** (conversational is cheap to
     re-route; an accidental crew run is expensive and noisy).

Every decision is logged and emitted to the UI as a `mode_switch` message
so users can see what Alfred interpreted. The manual switcher is always
available if the decision is wrong.

### The complementary-process flow

The modes aren't silos - they form a natural progression, and context
flows across mode transitions via `ConversationMemory`:

```
User: "what approval workflows do I have?"
  → orchestrator: insights
  → read-only handler runs `lookup_doctype("Workflow")` + `has_active_workflow()`
  → reply: "You have 2 active workflows: Leave Application (Draft→Approved→Rejected)
           and Material Request (Draft→Approved)."

User: "how would we approach adding one for Expense Claims?"
  → orchestrator: plan  (memory has the insights context)
  → plan crew: Requirement → Assessment → Architect → generate_plan_doc
  → reply: a structured plan doc with 4 steps, 1 open question

User: "use manager → finance. build it."
  → orchestrator: dev  (memory has a proposed plan; "build it" is a dev verb)
  → dev crew runs WITH the plan doc injected as the spec
  → standard changeset → preview → approve → deploy

User: "thanks"
  → orchestrator: chat
  → chat handler: "You're welcome! The workflow is live. Want a notification
                  to the approvers when a new claim is submitted?"
```

The key property: **no user action is needed to switch modes**. The
orchestrator reads the intent and the user just talks. Each of these
transitions reuses the same pipeline - only the downstream phases differ.

### Feature flag

Currently gated behind `ALFRED_ORCHESTRATOR_ENABLED=1` on the processing
app. When the flag is off, the orchestrate phase is a no-op and every
prompt runs through the Dev pipeline exactly as before - the sanitizer
fix that unblocked greetings is always on since it's an unambiguous bug
fix. Set the flag to `1` on the processing app to enable all three
non-Dev modes. The feature will be flipped to on-by-default after
production QA validates it across real customer prompts.

**Phase A (shipped):** orchestrator + chat mode + sanitizer fix for
unknown intents. The original bug that motivated this feature was that
typing "hi" returned *"Unable to classify prompt intent. Flagged for
admin review."* - the sanitizer hard-blocked any prompt without a Frappe
keyword. Phase A fixes the sanitizer and routes conversational prompts
to a proper chat handler.

**Phase B (shipped):** Insights mode. A single-agent CrewAI crew with a
read-only MCP tool subset and a tight 5-call budget answers questions
about the user's current site state and returns markdown. No changeset,
no DB writes. Fast-path prefixes (*"what X do I have?"*, *"list my..."*,
*"show me my..."*) route obvious queries without an LLM classifier call.
Insights Q/A pairs are recorded in `ConversationMemory.insights_queries`
so follow-up Plan/Dev turns can resolve references like *"that workflow
I asked about"*.

**Phase C (shipped):** Plan mode + cross-mode handoff. A 3-agent
planning crew (Requirement, Assessment, Architect) produces a structured
`PlanDoc` (title, summary, numbered steps, doctypes touched, risks,
open questions) rendered as a `PlanDocPanel` in the chat. The user
reviews the plan, clicks *Refine* to iterate or *Approve & Build* to
flip the plan status to `approved` and fire a Dev-mode run. The
`_phase_enhance` pipeline phase detects approval phrasing and injects
the full plan JSON into the enhanced prompt via
`ConversationMemory.render_for_prompt` so the Developer agent treats
the plan as an explicit spec. A plan is flipped to `built` after the
Dev run completes so it doesn't re-inject on the next turn.

**Phase D (shipped):** UI mode switcher + per-conversation
persistence. A 4-button `ModeSwitcher` component (Auto / Dev / Plan /
Insights) in the chat header lets the user force a specific mode. The
selection is persisted on `Alfred Conversation.mode` via the new
`set_conversation_mode` whitelisted endpoint and restored when the user
switches back to the conversation. Auto remains the default; forced
modes override the orchestrator's classification. `sendMessage` accepts
an optional per-turn mode override used by the Plan doc's "Approve &
Build" button.

---

## Example prompt, end to end

The example we'll follow through the rest of this doc:

> *"Send me an email when a new expense claim is submitted, so the
> approver knows to review it."*

The user (let's call them `alice@example.com`, role: HR Manager) types
this in the chat UI at `/app/alfred-chat`. Here's what happens,
step by step.

### Step 1: Send button click - `alfred_chat.send_message`

The Vue chat component calls the whitelisted method
`alfred_client.alfred_settings.page.alfred_chat.alfred_chat.send_message`
via Frappe's `frappe.call` helper. The call runs inside Frappe's web
server process, under Alice's session.

```python
# What runs on the Frappe web process:
validate_alfred_access()  # is Alice in Allowed Roles?
frappe.has_permission(    # does Alice have write on this conversation?
    "Alfred Conversation", ptype="write",
    doc=conversation, throw=True,
)

# Store the message as an Alfred Message row
msg = frappe.get_doc({
    "doctype": "Alfred Message",
    "conversation": conversation,
    "role": "user",
    "content": "Send me an email when ...",
}).insert()
frappe.db.commit()

# Kick off the connection manager (a background job on the long queue)
start_conversation(conversation)

# Push the message to a Redis durable queue the connection manager reads
redis.rpush(
    f"alfred:ws:outbound:queue:{conversation}",
    json.dumps({"msg_id": "...", "type": "prompt",
                "data": {"text": "...", "user": "alice@example.com"}}),
)
# ... and publish a wakeup notification so an already-running manager reads it
redis.publish(f"alfred:ws:outbound:{conversation}", "__notify__")
```

**Two permission checks** ran before anything happened: the coarse role
gate (Alice has an Alfred-allowed role) and the fine-grained
conversation check (Alice owns - or is shared on - this specific
conversation). Without the second check, any Alfred-role user could
inject messages into anyone else's chat. See
[SECURITY.md](SECURITY.md#authorization) for why this matters.

### Step 2: Connection manager - `_connection_manager`

`start_conversation` enqueues `_connection_manager(conversation_name,
user=conversation_owner)` on Frappe's RQ `long` queue. A worker
specifically configured for the `long` queue (2-hour timeout) picks it up.

Critically, `user` here is **the conversation owner, not Alice** - even
if Alice is triggering the send, the connection manager runs as whoever
originally created the conversation. If Alice were triggering a
conversation shared with her by Bob, the manager would still run as
Bob. Why? Because the MCP tools it dispatches need to run under Bob's
permissions to see Bob's view of the data. See the [Permission Model
section of how-it-works.md](how-it-works.md#permission-model).

The manager:

1. Calls `frappe.set_user(owner)` so `frappe.session.user` is correct
   for all downstream MCP tool dispatches.
2. Opens a WebSocket to the processing app (`ws://processing-host:8001/ws/<conversation_id>`).
3. Sends a handshake: `{api_key, jwt_token, site_config}`. The JWT is
   signed with the shared secret and embeds the owner's user + roles.
4. Subscribes to the Redis pub/sub channel + drains the durable queue
   in a loop.
5. Any message on the queue gets forwarded over the WebSocket.
6. Any inbound message from the WebSocket either goes to the browser
   (via `frappe.publish_realtime`) or gets dispatched as an MCP tool
   call.

### Step 3: Processing app receives the prompt

The processing app accepts the handshake:

```python
# alfred/api/websocket.py:_authenticate_handshake
jwt_payload = verify_jwt_token(handshake["jwt_token"], API_SECRET_KEY)
conn = ConnectionState(
    websocket=websocket,
    site_id=jwt_payload["site_id"],       # "dev.alfred"
    user=jwt_payload["user"],              # "bob@example.com" (the owner)
    roles=jwt_payload["roles"],
    site_config=handshake["site_config"],
)
conn.mcp_client = MCPClient(
    send_func=conn.send,
    main_loop=asyncio.get_running_loop(),
    ...
)
```

Then the next `prompt` message arrives. The WebSocket handler calls
`_run_agent_pipeline(conn, conversation_id, prompt_text)`.

In the current codebase (post Phase 3 state-machine refactor),
`_run_agent_pipeline` is a **thin wrapper** that builds a
`PipelineContext` and delegates to `AgentPipeline.run()`:

```python
# alfred/api/websocket.py
async def _run_agent_pipeline(conn, conversation_id, prompt):
    from alfred.api.pipeline import AgentPipeline, PipelineContext
    ctx = PipelineContext(conn=conn, conversation_id=conversation_id, prompt=prompt)
    await AgentPipeline(ctx).run()
```

The real work happens in `AgentPipeline.run()`, which iterates through
9 named phases. Each phase is a method, auto-wrapped in a tracer span,
and the orchestrator centralises error handling.

### Step 4: Pipeline phase 1 - `sanitize`

```python
async def _phase_sanitize(self):
    from alfred.defense.sanitizer import check_prompt
    result = check_prompt(self.ctx.prompt)
    if not result["allowed"]:
        self.ctx.stop(
            error=result["rejection_reason"],
            code="PROMPT_BLOCKED" if not result["needs_review"] else "NEEDS_REVIEW",
        )
```

The sanitizer runs a keyword-based intent classifier + a pattern-match
against known injection shapes. If the prompt looks like a prompt
injection attempt, the pipeline aborts immediately **before spending any
LLM tokens**. This is the cheapest possible guardrail - it doesn't catch
every attack, but it catches the obvious ones for free.

For our example prompt, `check_prompt` returns `{"allowed": True}` and
the phase completes.

### Step 5: `load_state` - Redis + conversation memory

```python
async def _phase_load_state(self):
    from alfred.state.store import StateStore
    from alfred.state.conversation_memory import load_conversation_memory

    redis = getattr(self.ctx.conn.websocket.app.state, "redis", None)
    self.ctx.store = StateStore(redis) if redis else None
    self.ctx.conversation_memory = await load_conversation_memory(
        self.ctx.store, self.ctx.conn.site_id, self.ctx.conversation_id
    )
    self.ctx.conversation_memory.add_prompt(self.ctx.prompt)
```

Redis stores **per-conversation memory** under a key like
`alfred:dev.alfred:task:conv-memory-abc123`. For a first-time prompt
the memory is empty; for a follow-up prompt it already contains the
items built in earlier turns + user clarifications.

For our example, this is the first prompt in the conversation, so
`conversation_memory.items == []` and the only thing we do is record
this new prompt. We'll revisit memory in the [multi-turn
section](#a-multi-turn-follow-up).

### Step 6: `plan_check` - admin portal gate (optional)

If `ADMIN_PORTAL_URL` + `ADMIN_SERVICE_KEY` are configured, the pipeline
calls the admin portal's `check_plan` endpoint:

```python
plan_result = await admin.check_plan(conn.site_id)
# Returns: {
#   "allowed": true,
#   "remaining_tokens": 85000,
#   "tier": "Pro",
#   "warning": null,
#   "pipeline_mode": "full"
# }
```

If `allowed=false`, the pipeline aborts with `PLAN_EXCEEDED`. If
`pipeline_mode` comes back, it's stored in `ctx.plan_pipeline_mode` and
will override the site's local setting in the `resolve_mode` phase.

For our self-hosted example there's no admin portal, so this phase is
a no-op.

### Step 7: `enhance` - the prompt enhancer

The raw prompt *"Send me an email when a new expense claim is submitted,
so the approver knows to review it"* is a bit informal. Agents do
better when the prompt is a precise spec:

```python
async def _phase_enhance(self):
    from alfred.agents.prompt_enhancer import enhance_prompt
    conversation_context = self.ctx.conversation_memory.render_for_prompt()
    self.ctx.enhanced_prompt = await enhance_prompt(
        self.ctx.prompt, self.ctx.user_context, self.ctx.conn.site_config,
        conversation_context=conversation_context or None,
    )
```

`enhance_prompt` is one focused LLM call (not the full crew). It
rewrites the prompt using a Frappe-aware system prompt:

> *"You are a Frappe/ERPNext expert. Your job is to take a user's raw
> request and rewrite it into a clear, detailed specification that a
> team of AI agents can execute..."*

The enhanced prompt for our example might look like:

```
Create a Notification DocType to alert the expense approver when a new
Expense Claim document is submitted.

- Target DocType: Expense Claim
- Trigger event: verify with the user whether they mean "New" (on insert)
  or "Submit" (on submission), since Expense Claim is submittable
- Recipients: the user in Expense Claim.expense_approver (Link to User)
- Channel: Email
- Subject: "New Expense Claim submitted: {{ doc.name }}"
- Message: HTML body with employee name, total amount, link to the doc

Open question for clarification:
- Should the notification fire on "New" (as soon as the employee creates
  the draft) or on "Submit" (after the employee explicitly submits)?
```

Note the **open question**. The enhancer intentionally surfaces
ambiguities rather than silently guessing - the next phase picks them up.

### Step 8: `clarify` - the blocking clarification gate

```python
async def _phase_clarify(self):
    from alfred.api.websocket import _clarify_requirements
    self.ctx.enhanced_prompt, self.ctx.clarify_qa_pairs = await _clarify_requirements(
        self.ctx.enhanced_prompt, self.ctx.conn, self.ctx.early_event_callback
    )
    if self.ctx.clarify_qa_pairs and self.ctx.conversation_memory is not None:
        self.ctx.conversation_memory.add_clarifications(self.ctx.clarify_qa_pairs)
```

`_clarify_requirements` is another focused LLM call with a strict system
prompt:

> *"You are a Frappe requirements analyst. You receive an enhanced user
> request and must identify BLOCKING ambiguities that only a human can
> resolve before any code is generated..."*

It returns a JSON array of at most 3 questions. For our example it
returns:

```json
[
  {
    "question": "Should the notification fire when the claim is first created (New) or when the employee submits it for approval (Submit)?",
    "choices": ["New (as soon as draft exists)", "Submit (after employee finalises)"]
  }
]
```

The processing app sends a `clarify` WebSocket event to the UI, and the
UI renders the question as a card with option buttons. Alice clicks
**"Submit"**. Her choice is sent back over the WebSocket as a
`user_response` message.

Back in the processing app, `conn.ask_human()` resolves the pending
future with the answer, and `_clarify_requirements` returns a clarified
prompt with a USER CLARIFICATIONS section appended:

```
[original enhanced prompt]

USER CLARIFICATIONS:
Q: Should the notification fire when the claim is first created or when the employee submits it?
A: Submit (after employee finalises)
```

The Q/A pair is also written into `conversation_memory.clarifications`
so future follow-up prompts in this conversation can honour the
decision without re-asking.

**Why is this worth a separate phase instead of just letting the crew
figure it out?** Because asking ONCE before running the crew costs about
200 tokens, while having the crew drift into the wrong assumption and
backtrack costs 20,000+. This is Alfred's single biggest accuracy
improvement - ship a blocking question gate early and let the human
resolve the ambiguity while it's cheap.

### Step 9: `resolve_mode` - full vs lite

```python
async def _phase_resolve_mode(self):
    if self.ctx.plan_pipeline_mode:
        self.ctx.pipeline_mode = self.ctx.plan_pipeline_mode
        self.ctx.pipeline_mode_source = "plan"
    else:
        mode = (self.ctx.conn.site_config.get("pipeline_mode") or "full").lower()
        if mode not in ("full", "lite"):
            mode = "full"
        self.ctx.pipeline_mode = mode
        self.ctx.pipeline_mode_source = "site_config"
```

Precedence: admin portal override > site config > `"full"` default.
For our example, no admin portal, no local override, so we run in
`"full"` mode (6 agents). The UI gets an `agent_status` event with
`pipeline_mode = "full"` so it can show the 6-phase indicator at the
top.

### Step 10: `build_crew` - assemble the CrewAI crew

```python
async def _phase_build_crew(self):
    from alfred.agents.crew import build_alfred_crew
    from alfred.tools.mcp_tools import build_mcp_tools, init_run_state

    self.ctx.custom_tools = build_mcp_tools(self.ctx.conn.mcp_client)
    init_run_state(self.ctx.conn.mcp_client, conversation_id=self.ctx.conversation_id)

    self.ctx.crew, self.ctx.crew_state = build_alfred_crew(
        user_prompt=self.ctx.enhanced_prompt,
        user_context=self.ctx.user_context,
        site_config=self.ctx.conn.site_config,
        custom_tools=self.ctx.custom_tools,
    )
```

Three things happen here:

1. **MCP tools are wrapped** - `build_mcp_tools` produces a dict of
   `@tool`-decorated wrappers that forward calls to the MCP client.
   Each wrapper is a CrewAI-compatible tool that the agents will see in
   their toolbox.

2. **Per-run state is attached** to the MCP client: budget cap
   (30 calls), dedup cache (identical `(tool, args)` returns cached
   result), failure counter, misuse detection. This is the Phase 1
   hardening - without it, a single pipeline could make 100+ redundant
   `get_doctype_schema` calls on the same doctype.

3. **The crew is built** with 6 agents + 6 tasks:
   - **Requirement Analyst** (task: `gather_requirements`)
   - **Feasibility Assessor** (task: `assess_feasibility`)
   - **Solution Architect** (task: `design_solution`)
   - **Frappe Developer** (task: `generate_changeset`) - the one that
     produces the actual JSON
   - **QA Validator** (task: `validate_changeset`)
   - **Deployment Specialist** (task: `deploy_changeset`)

Each upstream task (gather, assess, design) has a **handoff condenser**
callback attached. When the task finishes, the callback rewrites
`task_output.raw` in place to strip prose, keep just the structured
JSON, and tail-truncate the rest. Downstream tasks see the compact
form, not the verbose original. This is the Phase 2 optimization that
cut handoff context by ~60-70% without any extra LLM calls.

### Step 11: `run_crew` - the big one

```python
async def _phase_run_crew(self):
    from alfred.agents.crew import run_crew
    timeout = self.ctx.conn.site_config.get("task_timeout_seconds", 300)
    self.ctx.crew_result = await asyncio.wait_for(
        run_crew(
            self.ctx.crew, self.ctx.crew_state, self.ctx.store,
            self.ctx.conn.site_id, self.ctx.conversation_id, self.ctx.event_callback,
        ),
        timeout=timeout * 2,
    )
```

`run_crew` wraps `crew.kickoff()` and streams phase events back to the
UI. CrewAI is synchronous under the hood, so we run it inside a thread
pool executor. Duration: ~200-300 seconds for our example against a
local qwen2.5-coder:32b.

See [the next section](#what-happens-in-the-developer-agents-head) for
what the Developer agent actually does during those 200 seconds.

When the crew finishes, `run_crew` extracts the `generate_changeset`
task output (not the Deployer's - the Developer's output has the actual
JSON) and stores it in `ctx.crew_result`.

### Step 12: `post_crew` - extract, rescue, reflect, dry-run, preview

```python
async def _phase_post_crew(self):
    # Extract the changeset from the crew's final output
    self.ctx.changes = _extract_changes(self.ctx.result_text)

    # Rescue path if extraction returned empty
    if not self.ctx.changes:
        self.ctx.changes = await _rescue_regenerate_changeset(
            self.ctx.enhanced_prompt, self.ctx.result_text,
            self.ctx.conn.site_config, self.ctx.event_callback,
        )

    if not self.ctx.changes:
        self._send_error_later("Empty changeset", "EMPTY_CHANGESET")
        return

    # Phase 3 #13 reflection minimality
    self.ctx.changes, self.ctx.removed_by_reflection = await reflect_minimality(
        self.ctx.prompt, self.ctx.changes, self.ctx.conn.site_config,
    )

    # Pre-preview dry-run
    self.ctx.dry_run_result = await _dry_run_with_retry(
        self.ctx.conn, self.ctx.crew_state, self.ctx.changes,
        self.ctx.conn.site_config, self.ctx.event_callback,
    )
    self.ctx.changes = self.ctx.dry_run_result.pop("_final_changes", self.ctx.changes)

    # Save conversation memory
    self.ctx.conversation_memory.add_changeset_items(self.ctx.changes)
    await save_conversation_memory(...)

    # Send the changeset to the UI
    await self.ctx.conn.send({
        "type": "changeset",
        "data": {"conversation": ..., "changes": ..., "dry_run": ...},
    })
```

Five sub-steps:

**a. Extract.** The crew's final output is a string. We need a JSON
array. `_extract_changes` uses `json.JSONDecoder.raw_decode` to walk
the string and pick the first well-formed JSON object/array. This is
robust against qwen-style retry loops that produce 5+ repeated JSON
blocks plus `<|im_start|>` chat-template leakage (which the sanitizer
strips first).

**b. Rescue.** If extraction returned empty (the crew drifted into
pure prose), we make ONE focused litellm call with the original prompt
and a strict "raw JSON only" system prompt. This is the last-resort
fallback.

**c. Reflect minimality** (only if `ALFRED_REFLECTION_ENABLED=1`). A
small LLM call reviews the changeset against the original prompt and
drops items the user didn't ask for. Has a safety net: refuses to
strip all items. Skipped for single-item changesets.

**d. Dry-run validation.** This is where we prove the changeset would
actually work before showing it to Alice. See
[the next section](#dry-run-the-safety-net).

**e. Save + send.** Conversation memory gets updated with the new
items + clarifications. The changeset goes out as a `changeset`
WebSocket event. The UI slides the preview drawer in from the right.

### Step 13: The preview drawer

Alice sees the drawer slide in from the right edge (the toolbar
toggle picks up a red unseen-dot; clicking the drawer clears it):

```
┌──────────────────────────────────────────────────────────┐
│ ✓ Validated - ready to deploy                            │
│                                                           │
│ Changeset Preview                                         │
│ 1 operation(s)                                            │
│                                                           │
│ Notifications (1)                                         │
│                                                           │
│ create "Alert on New Expense Claim"                       │
│                                                           │
│ Document Type       Expense Claim                         │
│ Event               Submit                                │
│ Channel             Email                                 │
│ Subject             New Expense Claim submitted: {{...}}  │
│ Recipients          Field: expense_approver               │
│ Enabled             Yes                                   │
│                                                           │
│ Message template:                                         │
│ ┌──────────────────────────────────────────────────┐     │
│ │ <p>A new expense claim has been submitted:</p>   │     │
│ │ <ul>                                              │     │
│ │   <li>Employee: {{ doc.employee_name }}</li>     │     │
│ │   <li>Amount: {{ doc.total_claimed_amount }}</li>│     │
│ │ </ul>                                             │     │
│ └──────────────────────────────────────────────────┘     │
│                                                           │
│ [ Approve & Deploy ]  [ Request Changes ]  [ Reject ]    │
└──────────────────────────────────────────────────────────┘
```

The banner is **green** because the dry-run passed. If it had failed,
she'd see a red/orange banner with a list of critical/warning issues
and the Approve button would relabel to "Deploy Anyway" - she could
still proceed, but with eyes open.

This is the first place Alice sees the agent's full work product. Up
to this point, she was watching the floating status pill flip through
activity phrases *(`Reading Expense Claim schema...`, `Checking write
permission on Notification...`, `Validating changeset against live
site...`)* as each tool call fired. Now she has a static, structured
review surface in the drawer. She can minimize the drawer to a chip
at the bottom-right and reopen it any time.

---

## What happens in the Developer agent's head

Zooming in on one agent's inner loop, because this is where most of
the non-obvious design lives.

When the `generate_changeset` task starts, CrewAI hands the Developer
agent a task description and a toolbox. The task description is
carefully written:

```
OUTPUT FORMAT (STRICT): Your entire Final Answer MUST be a single JSON array.
Start with `[` and end with `]`. Do NOT include any prose, explanation, ...

THINK FIRST, ACT SECOND (reasoning discipline - not in Final Answer):
Before calling any tool, your very first Thought: MUST be a short numbered
plan of the exact documents you will create. Use this format in your
Thought block:
  PLAN:
  1. create <doctype> '<name>' - <why, 1 line>
  2. ...
Then, for each item in your plan, call lookup_doctype(<target>, layer='framework')
once to get the authoritative field list, and lookup_pattern(<name>, kind='name')
if a matching curated template exists.

TASK: Generate production-ready Frappe document definitions ...
```

The "THINK FIRST, ACT SECOND" preamble is **Phase 3 #15**, the
think-then-act planning step. The Developer's first LLM turn produces
something like:

```
Thought: PLAN:
  1. create Notification 'Alert on New Expense Claim' - email the
     expense_approver on Expense Claim Submit

  First, I'll verify the Expense Claim schema to find the approver
  field name.

Action: lookup_doctype
Action Input: {"name": "Expense Claim", "layer": "framework"}
```

This is a standard CrewAI ReAct loop: Thought → Action → Observation →
Thought → ... → Final Answer.

### The tool call

The `lookup_doctype` call gets wrapped by the MCP client and goes out
over the WebSocket as a JSON-RPC message:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "lookup_doctype",
    "arguments": {"name": "Expense Claim", "layer": "framework"}
  },
  "id": "<uuid>"
}
```

The connection manager on the Frappe side picks it up in `_listen_ws`,
routes it to `handle_mcp_request`, which looks up `lookup_doctype` in
the `TOOL_REGISTRY`, runs it under Bob's session, and returns:

```json
{
  "jsonrpc": "2.0",
  "id": "<uuid>",
  "result": {"content": [{"type": "text", "text": "{ ... schema ... }"}]}
}
```

The processing app's MCP client receives this, parses the inner JSON,
and resolves the future that the tool wrapper is awaiting. CrewAI gets
a string back as the Action's Observation, feeds it into the next
Thought turn, and the agent continues.

### Phase 1 tracking kicks in

Before the tool wrapper actually dispatches to the MCP client, it
checks the per-run tracking state attached to the client:

```python
def _mcp_call(mcp_client, tool_name, arguments):
    state = getattr(mcp_client, "run_state", None)
    if state is None:
        return mcp_client.call_sync(tool_name, arguments, timeout=30)  # old path

    # Budget check
    if state["calls_made"] >= state["call_budget"]:
        return json.dumps({"error": "budget_exceeded", ...})

    # Dedup cache check
    args_key = json.dumps(arguments, sort_keys=True)
    cache_key = (tool_name, args_key)
    if cache_key in state["dedup_cache"]:
        state["dedup_hits"] += 1
        return state["dedup_cache"][cache_key]

    # Misuse detection
    if tool_name in _TOOLS_REQUIRING_PRIOR_LOOKUP and not state["lookup_tools_called"]:
        notes = ["dry_run_changeset called before any schema lookup"]
        # ... dispatch + add the note to the response

    # Actually dispatch
    try:
        result = mcp_client.call_sync(tool_name, arguments, timeout=30)
        state["dedup_cache"][cache_key] = result  # cache for next call
        state["calls_made"] += 1
        ...
```

Three cheap checks that pay off a lot:

- **Budget cap**: caps runaway agents. If the Developer goes into a
  loop calling the same tool 40+ times, we return a `budget_exceeded`
  error and the agent surfaces that in its next Thought instead of
  spinning.
- **Dedup cache**: the same `(tool, args)` pair returns the cached
  result without round-tripping. Typical savings: 15-25% of MCP calls
  per pipeline run.
- **Misuse detection**: `dry_run_changeset` called before any schema
  lookup means the agent is validating without knowing what it's
  validating - we add a note to the response so the next Thought says
  *"I should look up the schema first"*.

All three are Phase 1 hardening. None of them involve LLM calls, all of
them run in microseconds.

### The agent produces a plan, then the code

After a few more tool calls (`lookup_doctype` for Expense Claim,
`lookup_pattern` for `approval_notification`, `check_permission` for
write on Notification), the Developer is ready. Its Final Answer is:

```json
[
  {
    "op": "create",
    "doctype": "Notification",
    "data": {
      "doctype": "Notification",
      "name": "Alert on New Expense Claim",
      "subject": "New Expense Claim submitted: {{ doc.name }}",
      "document_type": "Expense Claim",
      "event": "Submit",
      "channel": "Email",
      "recipients": [{"receiver_by_document_field": "expense_approver"}],
      "message": "<p>A new expense claim has been submitted...</p>",
      "enabled": 1
    }
  }
]
```

The handoff condenser callback fires, but for `generate_changeset` it's
a no-op - the Developer's output IS the changeset, we don't want to
compact it away. CrewAI moves to the next task (`validate_changeset`,
run by the Tester agent), which sees the compacted outputs of the
three upstream tasks (requirements, assessment, design) plus the
changeset itself.

---

## Dry-run: the safety net

The Developer produced a changeset. Before Alice sees it, the pipeline
runs the `dry_run_changeset` MCP tool. This is the single most
important safety feature in Alfred.

The tool runs on the Frappe side:

```python
# alfred_client/api/deploy.py
def dry_run_changeset(changes):
    for i, change in enumerate(changes):
        doctype = change["doctype"]

        # 1. Generic checks: doctype exists, operation is create/update,
        #    no name collision if create
        if not frappe.db.exists("DocType", doctype):
            issues.append({"severity": "critical", "issue": f"DocType '{doctype}' does not exist"})
            continue

        # 2. Runtime checks (cheap, don't touch DB):
        #    - Server Script: compile(script)
        #    - Notification: frappe.render_template(subject, message, condition)
        #    - Client Script: balanced-brace regex
        _check_runtime_errors(doctype, data)

        # 3. Dry-run the insert
        _dry_run_single(doctype, data, operation)
```

**Step 3 is where the interesting design choice lives.** The naive
approach would be:

```python
def _dry_run_single(doctype, data, operation):
    frappe.db.savepoint("dry_run")
    try:
        doc = frappe.get_doc({"doctype": doctype, **data})
        doc.insert(ignore_permissions=True)
    finally:
        frappe.db.rollback(save_point="dry_run")
```

**This is broken for DDL-triggering doctypes.** Here's why:

When you `.insert()` a Workflow document, Frappe's controller runs
`Workflow.on_update()` which calls `Custom Field.save()` to create a
`workflow_state` column on the target doctype. That `Custom Field`
save runs `ALTER TABLE tabLeave Application ADD COLUMN workflow_state`.

**MariaDB implicitly commits all pending DML before any DDL statement.**
That means the moment the ALTER runs, the savepoint is gone - along
with the Workflow row we were trying to "dry-run". The rollback has
nothing to roll back, and we've just quietly deployed a Workflow to
the DB. Alice never approved anything, but the change is now live.

Fix:

```python
_DDL_TRIGGERING_DOCTYPES = frozenset({
    "DocType", "Custom Field", "Property Setter",
    "Workflow", "Workflow State", "Workflow Action Master", "DocField",
})
_SAVEPOINT_SAFE_DOCTYPES = frozenset({
    "Notification", "Server Script", "Client Script", "Print Format",
    "Report", "Role", ...
})

def _dry_run_single(doctype, data, operation):
    if doctype in _DDL_TRIGGERING_DOCTYPES:
        _meta_check_only(doctype, data, operation)  # never calls .insert()
        return
    if doctype in _SAVEPOINT_SAFE_DOCTYPES:
        _savepoint_dry_run(doctype, data, operation)  # original path
        return
    _meta_check_only(doctype, data, operation)  # conservative default
```

For a DDL-triggering doctype, `_meta_check_only` does:

1. `frappe.get_doc(doc_data)` - instantiate only, catches bad shapes and
   unknown field names.
2. Doctype-specific semantic checks (e.g., for Workflow: states +
   transitions consistency, submittable alignment).
3. Meta-level mandatory field walk.
4. Link field target existence check.
5. **Never calls `.insert()` / `.save()` / `.validate()`**. The trade-off:
   we miss some controller-level validations, but we NEVER accidentally
   deploy during dry-run.

For our example, the Notification changeset goes through the savepoint
path. `Notification` is DML-only - no DDL cascades - so `.insert()`
inside a savepoint + rollback is completely safe. Alice sees a green
"✓ Validated - ready to deploy" banner because the savepoint insert
worked and was then rolled back cleanly.

See [how-it-works.md dry-run section](how-it-works.md#dry-run-validation)
for the full decision tree.

---

## Approval and deployment

Alice clicks **Approve & Deploy**. The UI calls
`alfred_chat.approve_changeset(changeset_name)`.

```python
@frappe.whitelist()
def approve_changeset(changeset_name):
    validate_alfred_access()

    # Authorization check (added in the security audit)
    frappe.has_permission(
        "Alfred Changeset", ptype="write", doc=changeset_name, throw=True,
    )

    cs = frappe.get_doc("Alfred Changeset", changeset_name)
    if cs.status != "Pending":
        frappe.throw("Not pending")

    # Parse changes
    changes = json.loads(cs.changes)

    # Second dry-run (belt-and-suspenders) - catches DB drift since preview
    dry_run = dry_run_changeset(changes)
    if not dry_run["valid"]:
        return {"status": "validation_failed", "issues": dry_run["issues"]}

    cs.status = "Approved"
    cs.save()
    frappe.db.commit()

    # Actually deploy
    return apply_changeset(changeset_name)
```

Three things worth calling out:

1. **Permission check** - `frappe.has_permission("Alfred Changeset",
   "write", ...)` routes through the `changeset_has_permission` hook,
   which delegates to the parent conversation's owner/share/System-Manager
   check. Without this, any user with the Alfred role could approve any
   other user's pending changeset. This was a real bug found in the
   security audit and fixed.

2. **Second dry-run** - we already ran a dry-run before showing the
   preview. Why again? Because between preview and approval, the DB
   state might have drifted - another admin could have created a
   conflicting doctype, or revoked a permission. The second dry-run
   catches this. If the two runs disagree, a warning is logged: "Dry-run
   disagreement for changeset ... preview-time valid=1, approve-time
   valid=0. Database state likely drifted."

3. **`apply_changeset` takes over** for the actual deploy.

### `apply_changeset`

```python
@frappe.whitelist()
def apply_changeset(changeset_name):
    validate_alfred_access()
    frappe.has_permission(
        "Alfred Changeset", ptype="write", doc=changeset_name, throw=True,
    )

    changeset = frappe.get_doc("Alfred Changeset", changeset_name)

    # Distributed lock: atomically transition Approved -> Deploying
    frappe.db.sql(
        "UPDATE `tabAlfred Changeset` SET status='Deploying' "
        "WHERE name=%s AND status='Approved'",
        changeset_name,
    )
    frappe.db.commit()
    changeset.reload()
    if changeset.status != "Deploying":
        frappe.throw("Already being deployed by another process.")

    # Switch to the conversation owner's context - ALL ops run as them
    conversation = frappe.get_doc("Alfred Conversation", changeset.conversation)
    requesting_user = conversation.user
    frappe.set_user(requesting_user)

    try:
        for i, change in enumerate(changes):
            doctype = change["doctype"]
            operation = change["op"]

            # Per-item permission re-check (layer 6 of the permission model)
            perm_action = "create" if operation == "create" else "write"
            if not frappe.has_permission(doctype, perm_action):
                raise frappe.PermissionError(...)

            # Write-ahead audit log BEFORE the operation
            _write_audit_log(
                changeset.conversation, doctype, doc_name, operation,
                before_state=_get_document_state(doctype, doc_name) if operation == "update" else None,
            )

            # Actually do it
            if operation == "create":
                result = _create_document(doctype, data)  # uses ignore_permissions=False
                rollback_data.append({"operation": "delete", "doctype": doctype, "name": result["name"]})
            elif operation == "update":
                before_state = _get_document_state(doctype, doc_name)
                result = _update_document(doctype, data)
                rollback_data.append({"operation": "restore", "doctype": doctype, "name": doc_name, "before_state": before_state})

            # Publish progress to the UI
            frappe.publish_realtime("alfred_deploy_progress", {...}, user=requesting_user)

        changeset.status = "Deployed"

    except Exception as e:
        # Roll back everything we did so far
        _execute_rollback(rollback_data, changeset.conversation)
        changeset.status = "Failed"
        raise
    finally:
        frappe.db.commit()
```

Seven safety features in this one function:

1. **Distributed lock**: the SQL UPDATE only succeeds if status is
   `Approved`. If two processes try to deploy the same changeset
   simultaneously, only one wins.
2. **Owner switch**: `frappe.set_user(conversation.user)` means every
   `.insert()` below runs with the owner's live permissions, not
   Administrator.
3. **Per-item permission re-check**: even though the pipeline already
   checked permissions at layer 4 (MCP session) and layer 5 (tool
   calls), we check ONE MORE TIME immediately before each write. If the
   owner's permissions changed since the preview, we abort that step.
4. **Write-ahead audit log**: `_write_audit_log` is called BEFORE the
   operation, not after. If the operation crashes, we still know what
   we tried. The audit log has `before_state` for updates (original
   snapshot) and will have an `after_state` written post-operation.
5. **Rollback data accumulates**: every successful write appends the
   inverse to `rollback_data`. If step 5 of 10 fails, `_execute_rollback`
   undoes steps 1-4 in reverse order.
6. **`ignore_permissions=False`**: the `.insert()` and `.save()` calls
   here enforce the full Frappe permission system. No shortcuts.
7. **Try/except with rollback + re-raise**: on ANY exception, we roll
   back, mark the changeset Failed, and re-raise so the UI surfaces the
   error.

Progress is streamed to Alice's browser via
`frappe.publish_realtime("alfred_deploy_progress", ...)`. She sees a
step-by-step progress tracker inside the preview drawer:

```
⏳ Alert on New Expense Claim (Notification)   In progress...
```

Then after a few seconds:

```
✓ All changes deployed successfully
```

She can navigate to `/app/notification/Alert on New Expense Claim` and
see her new notification document, fully configured.

---

## A multi-turn follow-up

Alice realises she wants the notification to also include the expense
amount in the subject line. She types:

> *"Actually, add the total amount to the subject of that notification."*

### What the pipeline does differently

Phase 1 (sanitize) and Phase 2 (load_state) run as before. But this
time `load_conversation_memory` returns:

```json
{
  "conversation_id": "abc123",
  "items": [
    {
      "op": "create",
      "doctype": "Notification",
      "name": "Alert on New Expense Claim",
      "on": "Expense Claim"
    }
  ],
  "clarifications": [
    {"q": "Should the notification fire on New or Submit?", "a": "Submit (after employee finalises)"}
  ],
  "recent_prompts": [
    "Send me an email when a new expense claim is submitted, ..."
  ],
  "updated_at": 1712947200.0
}
```

The memory layer's `render_for_prompt()` produces:

```
=== CONVERSATION CONTEXT (earlier in this chat) ===
Already discussed / built:
- create Notification "Alert on New Expense Claim" on Expense Claim

User decisions:
- Q: Should the notification fire on New or Submit?
  A: Submit (after employee finalises)

Recent prompts in this chat:
- Send me an email when a new expense claim is submitted...
=== END CONTEXT ===
```

This block is prepended to the prompt enhancer's user message. So the
enhancer sees:

```
USER REQUEST:
Actually, add the total amount to the subject of that notification.

=== CONVERSATION CONTEXT ===
... (the block above) ...
=== END CONTEXT ===
```

And now the enhancer can resolve *"that notification"* → `"Alert on
New Expense Claim"` without Alice having to respell it. The enhanced
prompt looks like:

```
Update the existing Notification "Alert on New Expense Claim" (on
Expense Claim, triggered on Submit - per the earlier clarification)
to include the total_claimed_amount field in the subject line.
```

The clarifier phase checks for ambiguities, finds none (the context is
unambiguous), and skips straight to the crew.

The Developer agent sees the enhanced prompt, looks up the existing
Notification via `lookup_doctype`, generates an **update operation**
(not a create - it recognises the doctype already exists), and returns:

```json
[
  {
    "op": "update",
    "doctype": "Notification",
    "data": {
      "doctype": "Notification",
      "name": "Alert on New Expense Claim",
      "subject": "New Expense Claim submitted: {{ doc.name }} ({{ doc.total_claimed_amount }})"
    }
  }
]
```

Alice sees the preview, approves, and the notification gets updated
in place. The whole second turn is ~45 seconds - much faster than the
first turn because the context is already narrowed.

### Why this matters

Without conversation memory, Alice would have had to type:

> *"Update the Notification called 'Alert on New Expense Claim' on
> Expense Claim to include the total_claimed_amount field in the
> subject line, keeping the Submit trigger we decided earlier."*

That's the kind of prompt a developer can write. Not the kind a busy
HR manager writes. Memory is what makes Alfred feel conversational
instead of form-like.

---

## When things go wrong

Two failure modes worth walking through.

### Failure 1: the Developer drifts into prose

Sometimes, especially with smaller local models, the Developer agent
loses focus and produces:

```
I analyzed the Expense Claim schema and found that the expense_approver
field is a Link to User. To create the notification, we would use:

  subject: "New expense claim from {{ doc.employee_name }}"
  document_type: "Expense Claim"
  event: "Submit"
  ...

I hope this helps! Let me know if you need anything else.
```

That's not JSON. `_extract_changes` returns empty. The pipeline falls
into the **rescue path**:

```python
if not self.ctx.changes:
    self.ctx.changes = await _rescue_regenerate_changeset(
        self.ctx.enhanced_prompt, self.ctx.result_text,
        self.ctx.conn.site_config, self.ctx.event_callback,
    )
```

`_rescue_regenerate_changeset` is one focused litellm call with:

- **System prompt**: very strict "return JSON array only, nothing else"
- **User message**: the original enhanced prompt + the failed output as
  "hints" (explicitly marked "IGNORE the format")
- **`temperature=0, max_tokens=2048, timeout=90`**

It's a single-shot retry without the agent loop. The UI sees a
`reformat` event (*"Crew drifted off-spec. Regenerating changeset
from the original request..."*) so Alice knows something happened. In
most cases the rescue produces a clean changeset and the pipeline
continues as normal. If rescue also fails, the pipeline emits an
`EMPTY_CHANGESET` error and Alice is asked to rephrase.

Why have this at all? Because pipeline restarts are expensive. Burning
~50k tokens on a full crew run and then giving up because the last
turn went off-format is wasteful. A single 1-2k token rescue call saves
the whole run most of the time.

### Failure 2: the dry-run rejects the changeset

The Developer produces a changeset that **shouldn't** deploy - maybe
a mandatory field is missing, or a Link target doesn't exist, or the
Python syntax in a Server Script is broken.

`dry_run_changeset` returns `valid=false` with a list of issues. The
pipeline doesn't give up immediately - instead, `_dry_run_with_retry`
spins up a mini-crew with **just the Developer** and hands it the
original changeset plus the dry-run issues:

```python
fix_task = Task(
    description=(
        "OUTPUT FORMAT (STRICT): Your entire Final Answer MUST be a single JSON array. "
        "Do NOT repeat the array. Do NOT include code fences. Do NOT include any prose "
        "or duplicate copies.\n\n"
        "The changeset you produced failed dry-run validation against the live site. "
        "Fix the issues below and produce a corrected changeset.\n\n"
        "Validation issues: {issues_json}\n"
        "Original changeset: {changes_json}\n\n"
        "Return a corrected changeset as a JSON array ..."
    ),
    expected_output="A JSON array of corrected changeset items (single array, no repeats).",
    agent=developer,
)
developer.max_iter = 3   # cap retries so qwen can't spin forever
```

The Developer reruns with the failure context in mind, produces a
corrected changeset, and we dry-run again. If the second dry-run passes,
we're good. If it still fails, we stop retrying (only ONE retry is
allowed) and show Alice the changeset with a red banner listing the
remaining issues. She can click **"Deploy Anyway"** if she thinks the
dry-run is a false positive, or **"Request Changes"** and explain what
to fix.

The `developer.max_iter = 3` cap is important. Without it, qwen-style
models sometimes enter a repetition loop producing the same near-miss
changeset over and over. The earlier failure mode we saw in manual QA
was exactly this: the Developer oscillating between two slightly
different Workflow JSON blocks, each missing a different required
field, for 10 minutes until the timeout fired. Capping iter = 3 + the
strict "no repeats" rule in the prompt fixed it.

---

## Rollback

A week later, Alice realises the notification is too noisy and wants
to undo it. She navigates to the Alfred Changeset record for her
original deployment and clicks **Rollback**.

```python
@frappe.whitelist()
def rollback_changeset(changeset_name):
    validate_alfred_access()
    frappe.has_permission(
        "Alfred Changeset", ptype="write", doc=changeset_name, throw=True,
    )

    changeset = frappe.get_doc("Alfred Changeset", changeset_name)
    if changeset.status != "Deployed":
        frappe.throw("Can only rollback deployed changesets")

    rollback_data = json.loads(changeset.rollback_data)

    # _execute_rollback walks the list in reverse and applies inverses:
    #   "delete" ops (for forward creates) → frappe.delete_doc
    #   "restore" ops (for forward updates) → doc.update(before_state)
    rollback_log = _execute_rollback(rollback_data, changeset.conversation)

    changeset.status = "Rolled Back"
    changeset.deployment_log = json.dumps(prev_log + rollback_log)
    changeset.save(ignore_permissions=True)
```

Because the original deploy captured `rollback_data`:

```json
[
  {"operation": "delete", "doctype": "Notification", "name": "Alert on New Expense Claim"}
]
```

the rollback simply deletes the Notification document. It's idempotent:
if the document was already deleted manually, the rollback logs a
"skipped" entry and moves on to the next item.

For **DocTypes with user data**, `_execute_rollback` has a smart path:
it checks `check_has_records` before deleting and if any data exists,
it skips the deletion and logs a message telling Alice to manually
review the DocType first. This prevents accidental data loss: if the
DocType was created a week ago and users have been entering records
into it, rolling back the creation would obliterate all those records.

Permission check is the **`write` on the changeset**, same as approve
and reject. Without this, any Alfred-role user could rollback any
deployed changeset and - because `_execute_rollback` uses
`ignore_permissions=True` internally + `force=True` on deletes - would
bypass the owner's normal permissions. Another real bug found in the
security audit.

---

## How the safety layers stack up

Let's count how many checks stand between Alice's prompt and an
actual DB write:

| Layer | What it enforces | When it runs |
|---|---|---|
| 1. Prompt defense | Injection-shaped prompts blocked | `sanitize` phase (before any LLM call) |
| 2. Plan check | Plan limits + tier-locked mode | `plan_check` phase (before crew spins up) |
| 3. Clarification gate | Blocking ambiguities resolved by a human | `clarify` phase (before crew spins up) |
| 4. Crew reasoning | 6 agents cross-check each other's output | `run_crew` phase (the main work) |
| 5. Reflection minimality | Optional drop-step for overreach | `post_crew` phase (if enabled) |
| 6. Pre-preview dry-run | Savepoint or meta-only validation | `post_crew` phase |
| 7. Preview + explicit approval | Human-in-the-loop, required by default | After the pipeline |
| 8. Approve-time dry-run | Second dry-run catches DB drift | Inside `approve_changeset` |
| 9. Distributed lock | No concurrent deploys of the same changeset | Inside `apply_changeset` |
| 10. Per-item permission re-check | Owner's live perms at write time | Inside the deploy loop |
| 11. `ignore_permissions=False` on writes | Full Frappe perm system at insert time | Inside `_create_document` / `_update_document` |
| 12. Write-ahead audit log | Every attempt recorded for forensics | Before each operation |
| 13. Rollback data accumulation | Inverse operations captured | After each operation |
| 14. Try/except with auto-rollback | Mid-deploy failure → undo what we did | Inside `apply_changeset` |

14 safety layers. Most systems have 2-3. The reason we have so many
is that **each layer catches a different failure mode**:

- Layers 1-5 prevent bad changesets from being *generated*.
- Layers 6-8 prevent bad changesets from being *approved*.
- Layers 9-14 prevent bad *deploys* even when approved changesets turn
  out to be wrong at write time.

If any single layer fails, the others still apply. A bug in the
prompt sanitizer doesn't mean an agent can deploy something Alice
didn't approve - the preview + explicit approval is still there.

---

## Why the design looks like this

A few deliberate choices that shape the whole system. Understanding
*why* helps when evaluating whether to keep them.

### Why not run CrewAI inside Frappe directly?

We tried. CrewAI pulls in chromadb, onnxruntime, pydantic-core,
litellm, and their transitive deps - hundreds of MB of Python packages,
some with compiled extensions that fight with Frappe's `bench` install.
Frappe sites are bench-global; adding these deps to one site means
adding them to all sites on that bench. Maintenance nightmare.

Splitting the AI work into a separate process keeps Frappe lean. A
customer can install `alfred_client` on their existing bench without
touching their Python environment - the client app is pure Python stdlib
+ Frappe. The processing app can live on any host that can open a
WebSocket back to the Frappe site.

### Why MCP instead of direct Frappe API calls?

The processing app could, in principle, hit Frappe's REST API (`/api/method/...`)
directly. We didn't, because:

1. **Permission model**: MCP calls travel over the same authenticated
   WebSocket as the chat protocol. The connection manager already set
   `frappe.session.user` to the conversation owner. REST calls would
   need to re-authenticate on every tool call and juggle session cookies.
2. **Latency**: a WebSocket round-trip is ~20-50ms. A REST call is
   ~100-300ms (TLS handshake + Frappe middleware + auth + deserialise).
   An agent that makes 30 tool calls per pipeline saves ~8 seconds on
   latency alone.
3. **Protocol fit**: MCP (Model Context Protocol, the Anthropic spec)
   was designed for exactly this use case - LLMs calling tools on
   external systems. It gives us `tools/list` + `tools/call` out of the
   box.
4. **Activity streaming**: every MCP tool call is also an opportunity
   to send an `agent_activity` event to the UI so the user sees concrete
   progress ("Reading Leave Application schema..."). Over REST we'd have
   to instrument every call separately.

### Why a state machine instead of a linear function?

The original pipeline was a 400-line imperative function with branches
and nested try/except. Adding a new phase meant surgery in the middle,
and testing a phase in isolation was effectively impossible without
booting the whole pipeline.

The Phase 3 architecture work rewrote this as `AgentPipeline.run()` -
10 phase methods over a shared `PipelineContext` (`sanitize`,
`load_state`, `plan_check`, `orchestrate`, `enhance`, `clarify`,
`resolve_mode`, `build_crew`, `run_crew`, `post_crew`). Each phase is
independently unit-testable (see `tests/test_pipeline_state_machine.py`).
Adding a new phase is two edits (add the method, append to `PHASES`).
Tracer spans wrap every phase automatically. Error handling is
centralised in one place.

The trade-off: the state machine is slightly more indirection for
someone reading the code for the first time - they have to jump from
`_run_agent_pipeline` to `AgentPipeline` to the phase methods. The
indirection pays off the moment you want to add a new phase or test
one in isolation.

### Why preview + explicit approval instead of auto-deploy?

Trust doesn't come pre-loaded. Users can't look at an agent-generated
changeset and predict whether it'll break their production system.
The preview + approval step is a 30-second speed bump that lets them
read the actual Jinja template, the actual field names, the actual
permissions, and say "yes that's what I meant" before it touches the
DB.

The `enable_auto_deploy` setting exists for specific SaaS deployments
where the customer has explicitly signed off on auto-deploying but we
recommend leaving it off. The amount of trust an AI changeset warrants
is still an open question.

### Why a handoff condenser (Phase 2) instead of shorter task descriptions?

CrewAI's sequential process passes each task's full raw output as
context into the next task. With 6 agents each producing ~2-4k tokens
of output, the Deployer sees ~15-20k tokens of predecessor chatter
before it starts. That's 60-70% of the total token spend per pipeline,
and most of it is redundant (the Architect's reasoning, the Assessor's
permission-check notes, etc. - none of which the Developer actually
needs).

The handoff condenser rewrites `task_output.raw` in place before the
next task reads it. It strips markdown fences, tries to parse the
output as JSON and re-emit it compactly, and tail-truncates as a
fallback. No extra LLM call - it's pure text processing on the
processing app. Savings: ~60-70% on handoff context, zero accuracy
regression on the benchmark suite.

### Why meta-only dry-run for DDL doctypes instead of running in a separate database?

We could spin up a shadow MySQL schema and point the dry-run at that.
That would give us full `.insert()` coverage on every doctype without
risking the real DB. But:

1. **Shadow schemas drift.** A Custom Field added to `tabLeave Application`
   on the real DB won't be on the shadow unless we sync. Dry-running
   against a stale shadow gives false validations.
2. **Compute cost.** A shadow schema means a separate MariaDB instance
   per customer site, or one big shared one with name mangling. Neither
   is cheap.
3. **Validation coverage**. The extra validations we'd gain by running
   the full `.insert()` are controller hooks. Most of those can be
   checked at meta level (mandatory fields, Link targets) or via
   doctype-specific logic (`_check_workflow`, `_check_custom_field`).
   The remainder is rare enough that catching them at approve-time
   dry-run is acceptable.

The meta-only path is the pragmatic middle ground: full safety on the
real DB, decent validation coverage, zero extra infrastructure.

---

## Where to go next

Now that you have the mental model, here's where to dig deeper
depending on what you're working on:

**If you're adding a feature:**

- [how-it-works.md](how-it-works.md) - system diagrams + component
  responsibilities
- [developing.md](developing.md) - full API reference + internal
  module contracts
- [how-it-works.md](how-it-works.md) - DocType field references + Redis
  key conventions

**If you're debugging a problem:**

- [running.md](running.md) - log markers, Redis commands, common
  pitfalls, pipeline tracing
- [running.md](running.md) - incident response runbooks, how to
  rotate keys, how to drain stuck queues

**If you're evaluating Alfred for production:**

- [SECURITY.md](SECURITY.md) - trust boundaries, authentication, data
  handling, production checklist
- [running.md](running.md) - service inventory, metrics to monitor,
  disaster recovery
- [getting-started.md](getting-started.md) - installation, cloud LLM providers, production
  deployment

**If you're optimizing performance:**

- [benchmarking.md](benchmarking.md) - running the benchmark harness,
  reading the JSON output, gate thresholds

**If you're a user who just wants to use it:**

- [getting-started.md](getting-started.md) - chat UI walkthrough, preview drawer,
  tips for writing better prompts
- [getting-started.md](getting-started.md) - quick start

**If you're a site admin configuring Alfred:**

- [getting-started.md](getting-started.md) - installation + connection setup
- [getting-started.md](getting-started.md) - Alfred Settings tabs, processing
  app env vars, admin portal fields

---

# Part 2 — Architecture Reference

The canonical reference for how the system is built: components, phases, state machines, permission model. Use this when you need a definitive answer to "how does X work" rather than "why does X exist".


## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Customer's Frappe Site                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐   │
│  │  Chat UI     │  │  MCP Server  │  │  Deployment Engine      │   │
│  │ /app/        │  │  (12 tools)  │  │  (changeset executor +   │   │
│  │ alfred-chat  │  │              │  │   rollback + audit log)  │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────────┘   │
│         │ Socket.IO        │ JSON-RPC             │                  │
│  ┌──────┴──────────────────┴──────────────────────┴──────────────┐  │
│  │           WebSocket Client (outbound connection manager)        │  │
│  │           alfred_client app                                     │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
└─────────────────────────────┼───────────────────────────────────────┘
                              │ WSS (client-initiated)
┌─────────────────────────────┼───────────────────────────────────────┐
│                Processing App (FastAPI, native dev / Docker prod)    │
│  ┌──────────────────────────┴────────────────────────────────────┐  │
│  │      WebSocket handshake: API key + JWT                        │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
│  ┌──────────┐  ┌────────────┴───────────────┐  ┌────────────────┐  │
│  │ Pipeline │→ │  AgentPipeline state       │←→│  MCP Client    │  │
│  │ Context  │  │  machine (15 phases)        │  │  (tools/call    │  │
│  │          │  │                             │  │   over same WS) │  │
│  └──────────┘  └────────────┬───────────────┘  └────────────────┘  │
│                             │                                       │
│                     ┌───────┴────────┐                               │
│                     │ Pipeline spans │  → JSONL trace file            │
│                     │  (tracer)      │     or stderr exporter         │
│                     └────────────────┘                               │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Redis   (conversation memory, crew state, event streams)     │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ HTTPS (service API key)
┌─────────────────────────────┼───────────────────────────────────────┐
│                Admin Portal (Frappe site)                            │
│  ┌──────────────┐  ┌───────┴──────┐  ┌──────────────────────────┐  │
│  │  Customers   │  │  Usage API   │  │  Billing / Subscriptions  │  │
│  │  & Plans     │  │  (check_plan,│  │  (Frappe Payments)        │  │
│  │              │  │ report_usage)│  │  (System-Manager gated)   │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
│                alfred_admin app                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Pipeline State Machine

Every user prompt runs through `AgentPipeline` - a linear state machine in
`alfred/api/pipeline.py`. Each phase is a named method that reads and mutates
a shared `PipelineContext`; the orchestrator iterates them in order, auto-wraps
each in a tracer span, and centralises error handling. Adding a new phase is
two edits (add the method, append to `PHASES`) instead of surgery in the middle
of a 400-line function.

```
User prompt
    │
    ▼
┌─────────────┐   Input sanitizer - blocks injection-shaped prompts
│  sanitize   │   before spending any LLM tokens.
└──────┬──────┘
       ▼
┌─────────────┐   Redis store + conversation memory load. Memory
│ load_state  │   is per-conversation, stores items/clarifications/
│             │   recent prompts so "now add X to that DocType"
│             │   resolves.
└──────┬──────┘
       ▼
┌─────────────┐   Pre-warm + strict health gate. Fires a 1-token
│   warmup    │   /api/generate against each distinct Ollama tier
│             │   model with keep_alive=10m so everything is loaded
│             │   before the crew runs. Doubles as a health probe.
│             │   Resilience: a process-local _WARMUP_CACHE stamps
│             │   (model, base_url) with monotonic-time on success;
│             │   follow-up prompts within 120s skip the probe
│             │   entirely. Each probe has 2 attempts with a 3s
│             │   backoff between, which absorbs the 3-8s window
│             │   Ollama takes to swap a tier model back into VRAM
│             │   after an idle gap. Failures evict the cached entry
│             │   so the next prompt re-probes. A genuinely unhealthy
│             │   Ollama still surfaces OLLAMA_UNHEALTHY with all
│             │   failing models listed; the failure count feeds
│             │   llm_errors_total{tier="warmup", error_type=
│             │   "OLLAMA_UNHEALTHY" | "probe_retry"} so you can
│             │   distinguish "real outage" from "transient reload".
│             │   Cloud providers (no ollama/ prefix) are skipped.
└──────┬──────┘
       ▼
┌─────────────┐   Admin-portal plan check. Returns allowed, remaining
│ plan_check  │   tokens, warning, and tier-locked pipeline_mode.
└──────┬──────┘
       ▼
┌─────────────┐   Three-mode chat orchestrator (Phase A+B+C+D).
│ orchestrate │   Classifies the prompt into dev / plan / insights / chat.
│             │   Uses a fast-path for obvious cases (greetings,
│             │   imperative build verbs, read-only query prefixes,
│             │   analytics verbs "show top N" / "list the top" /
│             │   "count of" / "summary of" / "report on" -> insights;
│             │   deploy verbs "build a report" / "create a report"
│             │   still beat analytics and route to dev) then falls
│             │   through to a short LLM call. Respects a manual
│             │   override from the UI switcher (Phase D ModeSwitcher
│             │   + Alfred Conversation.mode). Chat, Insights, and Plan
│             │   modes short-circuit here: their handler runs inline
│             │   and emits chat_reply / insights_reply / plan_doc. Dev
│             │   mode continues through the rest of the pipeline. Phase
│             │   C also adds Plan -> Dev handoff via ConversationMemory
│             │   .active_plan (approved plans are injected into
│             │   _phase_enhance as an explicit spec). Gated by
│             │   ALFRED_ORCHESTRATOR_ENABLED - when off the phase is a
│             │   no-op and the pipeline behaves as pre-feature.
│             │   Insights handler returns an InsightsResult with an
│             │   optional ReportCandidate that the client uses to
│             │   render a "Save as Report" button
│             │   (ALFRED_REPORT_HANDOFF).
└──────┬──────┘
       ▼
┌─────────────┐   Dev-mode intent classifier (V1). Classifies the
│classify_    │   dev-mode prompt into one of 22 intents across four
│intent       │   family builders (Schema, Reports, Automation,
│             │   Presentation) or "unknown". Two paths:
│             │   (a) Handoff short-circuit: if the prompt carries a
│             │       __report_candidate__ JSON trailer (user clicked
│             │       "Save as Report" on an Insights reply), force-
│             │       classify intent=create_report, source=handoff,
│             │       no LLM call. ctx.report_candidate carries the
│             │       structured spec for downstream phases.
│             │   (b) Normal path: heuristic substring matcher first
│             │       (e.g. "create a doctype" -> create_doctype;
│             │       "email the approver" -> create_notification;
│             │       "letterhead" -> create_letter_head), LLM
│             │       fallback constrained to the supported intent
│             │       list. Family-specific patterns are ordered
│             │       BEFORE generic ones so "add a role on X
│             │       doctype" matches create_role_with_permissions,
│             │       not create_doctype.
│             │   Gated by ALFRED_PER_INTENT_BUILDERS. When off, phase
│             │   is a no-op and the generic Developer runs downstream.
└──────┬──────┘
       ▼
┌─────────────┐   Dev-mode module classifier (V2/V3). Picks the target
│classify_    │   ERPNext module (Accounts, HR, Stock, ...) so the
│module       │   Developer specialist gets module-specific context
│             │   injected into its prompt and its changeset validated
│             │   against module conventions. Heuristic first
│             │   (ModuleRegistry.detect or .detect_all when
│             │   ALFRED_MULTI_MODULE is on): target_doctype matches
│             │   the registry -> high confidence; keyword hits ->
│             │   medium. V3 additionally returns up to 2 secondary
│             │   modules for cross-domain prompts ("Sales Invoice that
│             │   auto-creates a project task" -> primary=accounts,
│             │   secondaries=[projects]). LLM fallback is primary-only
│             │   to cap token budget. Gated by
│             │   ALFRED_MODULE_SPECIALISTS (V2) and ALFRED_MULTI_MODULE
│             │   (V3). Both off -> no-op.
└──────┬──────┘
       ▼
┌─────────────┐   Prompt enhancer - one focused LLM call rewrites the
│  enhance    │   raw prompt into a Frappe-aware spec. Conversation
│             │   memory is injected so the enhancer can resolve
│             │   references from earlier turns. Skipped when the
│             │   orchestrator picked a non-dev mode.
└──────┬──────┘
       ▼
┌─────────────┐   Structured clarification gate - if the enhanced
│  clarify    │   prompt has blocking ambiguities (trigger events,
│             │   recipient targets, scope, permissions), asks the
│             │   user up to 3 questions BEFORE spending crew tokens.
│             │   Answers are persisted into memory.
└──────┬──────┘
       ▼
┌─────────────┐   Auto-inject relevant Frappe KB entries + live site
│  inject_kb  │   state into the enhanced prompt before the crew runs.
│             │   Two retrievals, one combined banner:
│             │     (a) Hybrid keyword + semantic search over the Frappe
│             │         Knowledge Base (rules / APIs / idioms / style).
│             │         Processing-local - reads the YAML source-of-
│             │         truth directly, no MCP round-trip. Semantic
│             │         falls back to keyword-only if sentence-
│             │         transformers isn't available.
│             │     (b) Site reconnaissance: extract target DocType(s)
│             │         from the prompt via _DOCTYPE_NAME_RE, call the
│             │         get_site_customization_detail MCP tool, render
│             │         existing workflows / server scripts / custom
│             │         fields / notifications into a SITE STATE block.
│             │   Both render into one banner with a clear USER REQUEST
│             │   marker separating reference from ask. Fails open:
│             │   FKB failure doesn't block site recon, site recon
│             │   failure doesn't block FKB. Dev mode only.
│             │   ctx.injected_kb + ctx.injected_site_state logged to
│             │   the tracer so "the agent still got it wrong" can be
│             │   triaged as "rule wasn't injected" vs. "rule was
│             │   injected but ignored".
└──────┬──────┘
       ▼
┌─────────────┐   Resolve full vs lite pipeline mode:
│resolve_mode │     plan override > site config > default "full"
└──────┬──────┘
       ▼
┌─────────────┐   Module specialist provides domain context (V2/V3).
│provide_     │   For the classified primary module, calls
│module_      │   provide_context which returns a prompt snippet of
│context      │   relevant conventions/roles/gotchas; result cached
│             │   (Redis preferred, process-local fallback, 5-min
│             │   TTL). V3 fans out to secondaries in parallel and
│             │   merges into a single context block with clear
│             │   PRIMARY MODULE / SECONDARY MODULE CONTEXT headers.
│             │   Silent failure per module so one specialist down
│             │   doesn't block the pipeline. No-op when
│             │   ALFRED_MODULE_SPECIALISTS off or module=None.
└──────┬──────┘
       ▼
┌─────────────┐   Build the CrewAI crew with MCP-backed tools +
│ build_crew  │   per-run tracking state (budget, dedup cache,
│             │   failure counter - Phase 1 tool hardening).
│             │   When ALFRED_PER_INTENT_BUILDERS is on and the
│             │   classified intent has a specialist, the generic
│             │   Developer is swapped for a family builder (Schema,
│             │   Reports, Automation, or Presentation) bound to the
│             │   specific intent, and the generate_changeset task
│             │   description gains a registry-driven checklist of
│             │   shape-defining fields plus the module context
│             │   snippet assembled in provide_module_context.
└──────┬──────┘
       ▼
┌─────────────┐
│  run_crew   │   Kick off the crew (full 6-agent SDLC or lite single
│             │   agent). Handoff condenser callbacks compact each
│             │   upstream task's output in place so downstream tasks
│             │   see only the structured JSON, not the verbose prose.
└──────┬──────┘
       ▼
┌─────────────┐   Extract changeset from the crew's final output.
│ post_crew   │   Falls through a cascade:
│             │
│             │     _extract_changes (JSONDecoder.raw_decode picks
│             │        first well-formed block; handles qwen chat-
│             │        template leakage and repeated concatenated
│             │        JSON arrays)
│             │          │
│             │          ▼ empty?
│             │     _rescue_regenerate_changeset (one focused
│             │        LLM call from the original prompt)
│             │          │
│             │          ▼
│             │     backfill_defaults_raw (V1/V2/V3) - fills missing
│             │        registry fields from the intent registry
│             │        defaults, layers primary module's
│             │        permissions_add_roles and naming pattern on
│             │        top, plus each secondary module's permissions
│             │        when V3 is on. Every defaulted field records
│             │        field_defaults_meta with source + rationale so
│             │        the preview can render "default" pills.
│             │          │
│             │          ▼
│             │     module specialist validate_output (V2) - primary
│             │        module's validation notes keep full severity
│             │        and blockers gate deploy. V3 fans out to
│             │        secondaries and caps their blockers to warning.
│             │        Notes (deterministic rules + LLM pass, deduped
│             │        by normalised issue text) surface in the
│             │        preview panel grouped by source module.
│             │          │
│             │          ▼
│             │     reflect_minimality (Phase 3 #13, feature-flagged
│             │        via ALFRED_REFLECTION_ENABLED) - small LLM call
│             │        drops items that are NOT strictly needed.
│             │        Safety net refuses to strip all items.
│             │          │
│             │          ▼
│             │     _dry_run_with_retry (MCP dry_run_changeset + one
│             │        self-heal retry with just the Developer agent)
│             │          │
│             │          ▼
│             │     Save conversation memory, send changeset to UI
│             │     (payload carries changes + field_defaults_meta +
│             │     module_validation_notes + detected_module +
│             │     detected_module_secondaries).
└─────────────┘
```

Phases abort early by calling `ctx.stop(error, code)`; the orchestrator emits
the error after the phase loop exits. Exception boundaries are centralised in
`AgentPipeline.run()`: `asyncio.TimeoutError` -> `PIPELINE_TIMEOUT`, any other
exception -> `PIPELINE_ERROR`. Every phase is unit-testable in isolation.

### Dry-run validation: where the DDL vs savepoint split lives

The processing app calls `dry_run_changeset` as a single MCP tool; the decision
about HOW to validate each item happens on the **client (Frappe) side**, in
`alfred_client/alfred_client/api/deploy.py`. This is a deliberate split:

- **Processing side** (`alfred_processing/alfred/api/websocket.py:_dry_run_with_retry`)
  makes one MCP call with the full changeset and gets back
  `{valid, status, issues, validated}`. It does NOT classify items by
  doctype. It only attaches the `status` tag to distinguish `ok` /
  `invalid` / `infra_error` / `skipped` for the UI.

- **Client side** (`deploy.py::_dry_run_single`) routes each item to one
  of two validation paths based on its doctype:

  | Path | Doctypes | Rationale |
  |---|---|---|
  | `_meta_check_only` | `_DDL_TRIGGERING_DOCTYPES` (DocType, Custom Field, Property Setter, Workflow, Workflow State, Workflow Action Master, DocField) + all unknown doctypes | `.insert()` would trigger DDL (CREATE/ALTER TABLE) either directly or via controller side effects. MariaDB implicitly commits all pending DML before any DDL statement, which silently destroys the savepoint rollback - the intended "test insert" lands for real. We never call `.insert()` on these; instead we validate field shapes + links against `frappe.get_meta()`. |
  | `_savepoint_dry_run` | `_SAVEPOINT_SAFE_DOCTYPES` (Notification, Server Script, Client Script, Print Format, Letter Head, Report, Dashboard, Dashboard Chart, Role, Custom DocPerm, User Permission, Translation, Web Form, Web Page) | Pure DML. `.insert()` writes one row with no schema side effects. Savepoint-rollback catches controller-level validators (uniqueness, format checks, workflow rules) that the meta-only path misses. |

Both sets live on the client side because only the client's Frappe runtime
knows which doctypes trigger DDL (the list depends on installed apps and
Frappe version). The processing app is deliberately oblivious to this - it
just asks "validate this" and trusts the client's routing.

**Contract for future work:** any new doctype added to Alfred's generation
catalog needs an explicit entry in one of the two frozensets. Unknown
doctypes default to `_meta_check_only` for safety. Moving a doctype from
`_DDL_TRIGGERING_DOCTYPES` to `_SAVEPOINT_SAFE_DOCTYPES` requires proving
its controller path doesn't call `frappe.db.commit()` or trigger schema
changes - a mistake here deploys test inserts to production.

### User-initiated cancel (graceful stop)

The chat UI Stop button pushes a `{"type": "cancel"}` message through the
existing durable Redis queue to `alfred_client.api.websocket_client.cancel_run`.
The processing app's `_handle_custom_message` receives it, looks up
`conn.active_pipeline_ctx`, and calls `ctx.stop("Cancelled by user",
code="user_cancel")`. The pipeline loop checks `ctx.should_stop` at the next
phase boundary and exits via the same `_send_error` path as any other stop.
`_send_error` branches on the `user_cancel` code: instead of emitting a
generic `error` WS event (which would trip the rescue/retry path and render
a red banner) it emits a distinct `run_cancelled` event, which the client
routes as `alfred_run_cancelled` and renders as a neutral system message.
The Alfred Conversation row is marked `Cancelled` in the same request so
the UI stays honest even if the processing app is unreachable.

### Refresh-safe chat state

The chat UI reconstructs the full conversation view after a page reload
from the Frappe DB alone. No browser storage is trusted. Four pieces
cooperate:

1. **Alfred Conversation fields** cache volatile run state:
   `current_agent`, `current_activity`, `pipeline_mode`. `current_agent`
   existed previously but was written only by `escalation.py`; it is now
   overwritten by every `agent_status` event from the processing app.
   `agent_activity` writes `current_activity` (truncated to 140 chars).
   Any terminal event (error, run_cancelled, chat_reply, insights_reply,
   plan_doc, preview, changeset) clears the ticker fields so stale state
   does not leak across runs. `pipeline_mode` is preserved so the phase
   pipeline UI can draw the right number of phases even after the run
   ended.

2. **Alfred Changeset rows** are the source of truth for preview drawer
   state. Every published `preview`/`changeset` event inserts a row with
   `status=Pending`. Approve flips the row to `Deploying` then
   `Deployed` (or `Rolled Back` on failure). Rollback (user-initiated or
   automatic) flips to `Rolled Back` and appends rollback entries to
   `deployment_log`. The UI never holds deploy state in sibling Vue refs
   - it derives from the row.

3. **`get_conversation_state(conversation)`** is the single rehydrate
   endpoint. It returns the cached run state plus three changeset slots:
   `pending_changeset`, `deployed_changeset`, `failed_changeset`. At most
   one is expected to be non-null at a given moment but all three are
   returned so the UI can resolve status-transition races.

4. **`PreviewPanel` state machine** consumes the rehydrate payload and
   renders one of ten explicit states (EMPTY, WORKING, VALIDATING,
   DEPLOYING, PENDING, DEPLOYED, ROLLED_BACK, FAILED, REJECTED,
   CANCELLED) via a single `previewState` computed prop. Each state
   maps to a specific header, body layout, and action set. The
   distinction between ROLLED_BACK (user-initiated on a deployed
   changeset) and FAILED (auto-rollback from a mid-deploy failure) is
   derived from `deployment_log`: a failed step implies FAILED, success
   entries only implies ROLLED_BACK.

`AlfredChatApp.vue` picks the preview variant by priority pending >
deployed > failed, sets `isDeployed` accordingly, and passes
`conversationStatus` + `isProcessing` to `PreviewPanel` so the EMPTY
variant can render mode-specific copy ("Run cancelled", "Conversation
complete", "The previous run failed"). The transcript and the preview
drawer scroll independently; on desktop the drawer shoves the
transcript left via `body.alfred-drawer-open` (see "Shell layout"
below). The root `.alfred-app` is clamped to
`calc(100vh - var(--navbar-height, 60px))` scoped to
`body.alfred-page-active` so the chat page becomes a single scroll
boundary: `.alfred-transcript-scroll` handles the message scroll and
everything outside it (topbar, status pill, composer) stays pinned.
Without that clamp, `.alfred-app` would inherit auto-height from
Desk's `.layout-main-section` and a long conversation would scroll
the entire page, dragging the pinned surfaces off-screen. A scroll
listener on the transcript toggles
`.alfred-status-pill-anchor--scrolled` past 8px, which collapses the
pill's activity text into a tight "agent + elapsed" chip via CSS
descendant rules (no prop-drill into `AgentStatusPill`).

### Frontend design system

Every surface (chat transcript, preview states, sidebar rows, toolbar
chrome, plan-doc panel) composes from a small vocabulary defined in
`alfred_client/alfred_settings/page/alfred_chat/alfred_chat.css`. New
UI work should reuse these primitives rather than inventing gradients,
radii, or tone sets:

| Primitive | Purpose |
|---|---|
| `.alfred-mark` (`--chat`, `--preview`, `--sm`, `.alfred-mark-pulse`) | Gradient hero glyphs used by EMPTY / WORKING / brand / DEPLOYED. |
| `.alfred-card` (`--hover`, `--choice`, `--muted`, `--warn`, `--info`) | Workhorse container: conv rows, hint cards, plan steps, preview blocks. |
| `.alfred-chip` with tone modifiers (`--info / --success / --warn / --danger / --neutral / --finished`) and mode modifiers (`--auto / --dev / --plan / --insights`) | Role pills, mode badges, status indicators. |
| `.alfred-banner` (same tone modifiers) | Replaces validation-ok / -warn, status-rolled-back, deploy-success, saturation-amber / -red. |
| `.alfred-step` + `.alfred-step-dot` (`--done / --current / --failed`) | Vertical progress rows shared by transcript agent-step, preview DEPLOYING stream, and `PhasePipeline`. |
| `.alfred-btn-primary` (`--success / --warn`), `.alfred-btn-ghost` (`--danger`), `.alfred-btn-spinner` | First-class Alfred buttons. Replaces raw Bootstrap `btn btn-success / -warning / -default / -danger` inside the chat + preview. |
| `.alfred-eyebrow` | 11px uppercase section label (ticker "Live", activity log, plan "Steps" / "Risks"). |

Tokens (`--alfred-mark-*`, `--alfred-mode-*`, `--alfred-tone-*`,
`--alfred-card-radius / --alfred-chip-radius / --alfred-card-shadow`)
are scoped under `.alfred-app, .alfred-page` so nothing leaks into
Frappe Desk. Frappe's empty `.navbar-breadcrumbs` strip is hidden
while the chat page is mounted via a `body.alfred-page-active` class
toggled in `AlfredChatApp.vue`'s `onMounted` / `onUnmounted`.

### Shell layout (conversation-first)

The page is a single column. The two-panel flex split and the
draggable splitter are retired; the preview is a slide-in drawer
instead. Layout tree:

```
<div class="alfred-app">
  <ConversationList v-if="!currentConversation" />
  <div v-else class="alfred-chat-area">
    <div class="alfred-topbar">           <!-- frosted 48px -->
      [back] [mark] title | ModeSwitcher | [preview-toggle] [+ New] [...]
    </div>
    <div class="alfred-transcript">
      <AgentStatusPill absolute top-center />
      <div class="alfred-transcript-scroll">
        <div class="alfred-messages"> ... </div>
      </div>
      <div class="alfred-composer-wrap">
        <!-- saturation banner + activity log + .alfred-composer -->
      </div>
    </div>
  </div>
  <PreviewDrawer v-if="currentConversation" v-model="drawerOpen" />
  <button class="alfred-preview-minimized-pill" />
</div>
```

Two new SFCs carry the shell-specific UI:

- `AgentStatusPill.vue` - the floating status indicator. Renders one of
  four states (`idle / processing / outcome-success / outcome-error`)
  driven by the `statusPillState` computed in `AlfredChatApp.vue`.
  Processing state is clickable to expand a popover that embeds the
  existing `PhasePipeline` component for the full six-step trail.
- `PreviewDrawer.vue` - wraps the existing `PreviewPanel` inside a
  slide-in overlay with a head (minimize + close) and a body that
  hosts `PreviewPanel` unchanged. Mobile: modal with dimming scrim
  (`role="dialog"`, focus-trap via the close button). Desktop:
  non-modal (`role="complementary"`) and `body.alfred-drawer-open`
  shoves `.alfred-chat-area` left by the drawer width with a 280ms
  transition.

Drawer open state is persisted in `localStorage.alfred_chat_drawer_open`
and auto-opens on changeset arrival (watcher on `previewChangeCount`).
Escape-key precedence for closing surfaces: overflow menu -> status
popover -> drawer. Both the toolbar toggle and the floating
minimized-pill (bottom right) reopen the drawer.

## Multi-Model Tiers

Standalone LLM calls (outside CrewAI) and CrewAI agents can use different
models, configured per-tier in Alfred Settings > LLM Configuration >
"Per-Stage Model Overrides":

| Tier | Call sites | Purpose |
|------|-----------|---------|
| **Triage** | Classifier, Chat, Reflection | Short structured JSON, fast |
| **Reasoning** | Enhancer, Clarifier, Rescue | Domain reasoning, mid-weight |
| **Agent** | Dev/Plan/Insights/Lite crews | Tool use + code generation |

Empty tier fields fall back to the default model. Resolution lives in
`alfred/llm_client.py:_resolve_ollama_config_for_tier` (standalone calls)
and `alfred/agents/definitions.py:_resolve_llm_for_tier` (CrewAI agents).

Standalone calls use `alfred/llm_client.py` (urllib-based, not litellm)
because litellm's httpcore/anyio transport hangs when called from a thread
executor inside an asyncio event loop. CrewAI still uses litellm internally.

## Crew Modes

### Full Mode (6 agents, sequential)

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  Requirement │──▶│  Assessment  │──▶│  Architect   │──▶│  Developer   │
│  Analyst     │   │  Assessor    │   │  Designer    │   │  (changeset) │
└──────────────┘   └──────────────┘   └──────────────┘   └──────┬───────┘
       │                  │                  │                  │
       ▼                  ▼                  ▼                  ▼
   handoff            handoff            handoff         (no condense -
   condense           condense           condense         changeset is
                                                          the artifact)
                                                                │
                                                         ┌──────▼───────┐
                                                         │  Tester      │
                                                         └──────┬───────┘
                                                                │
                                                         ┌──────▼───────┐
                                                         │  Deployer    │
                                                         └──────────────┘
```

- `Process.sequential` - no manager agent, no delegation loops.
- **Handoff condenser** (Phase 2): each upstream task's raw output is
  compacted in place via a `Task.callback` before the next task reads it.
  Strips prose, picks out the JSON, tail-truncates fallback. Reduces handoff
  context ~60-70% without another LLM call. `generate_changeset`,
  `validate_changeset`, and `deploy_changeset` are skipped so the changeset
  artifact survives unchanged.
- **Think-then-act planning** (Phase 3 #15): `generate_changeset` forces the
  Developer to emit a numbered 1-6 item PLAN in its first Thought before any
  tool call. The plan stays in the reasoning channel, Final Answer is raw
  JSON only.
- **MCP tool hardening** (Phase 1): each tool call goes through a per-run
  wrapper that enforces a hard budget cap, dedupes identical calls, counts
  failures, and caches successful results by `(conversation_id, tool, args)`.
- **Condensation happens synchronously** via `Task.callback` so it
  doesn't add latency vs the upstream task's own duration.

### Lite Mode (single agent, ~5x faster)

```
User prompt
    │
    ▼ (enhance_prompt + clarify)
┌────────────────────────────────────────────┐
│  Alfred Lite  (role: Frappe Developer)     │
│  - Fused backstory: requirements + design  │
│    + codegen                                │
│  - max_iter=4                               │
│  - Tools: union of all specialist tools     │
│    + consolidated Framework KG tools        │
│    (lookup_doctype, lookup_pattern)         │
│  - THINK FIRST, ACT SECOND preamble         │
└───────────────────┬────────────────────────┘
                    │
                    ▼
              Crew output extract
                    │
                    ▼
           Reflection minimality (opt)
                    │
                    ▼
           Pre-preview dry-run
                    │
                    └─▶ same preview + approve flow as full
```

Lite mode is selected when `Alfred Settings.pipeline_mode = "lite"` OR when the
admin portal's `check_plan` response sets `pipeline_mode: "lite"`. Plan override
always wins over local setting. Single-agent mode trades cross-agent validation
for ~5x lower LLM cost and ~5x faster completion. The pre-preview dry-run +
approve-time safety net still catch insert-time errors, so broken changesets
are blocked regardless of mode.

## Specialist Stack (V1-V4)

Four layered feature flags progressively specialise Dev mode's Developer
agent. Each flag is a strict extension of the one below; when off, the
pipeline path matches the layer underneath exactly. All four are additive
and regression-safe - turning them off at any time reverts behaviour to the
pre-feature Developer.

### V1 Per-intent Builder specialists (`ALFRED_PER_INTENT_BUILDERS`)

The generic Developer agent is swapped for an intent-specific specialist
when the classified intent has a Builder registered.

```
Dev-mode prompt
    │
    ▼
classify_intent → one of 22 intents across four families, or "unknown"
    │
    ▼
build_alfred_crew:
  if intent in SCHEMA_INTENTS:
      agents["developer"] = build_schema_agent(intent, ...)
  elif intent in REPORTS_INTENTS:
      agents["developer"] = build_reports_agent(intent, ...)
  elif intent in AUTOMATION_INTENTS:
      agents["developer"] = build_automation_agent(intent, ...)
  elif intent in PRESENTATION_INTENTS:
      agents["developer"] = build_presentation_agent(intent, ...)
  else:
      agents["developer"] = generic Developer (pre-V1)
  task description += render_registry_checklist(intent_schema)
    │
    ▼
crew runs → ChangesetItem
    │
    ▼
backfill_defaults_raw: intent-registry defaults layered with
  field_defaults_meta tagging source=user|default|needs_clarification
  per field
```

**Family builders**: four Dev-mode specialists group related intents under
a shared family backstory that teaches controller-enforced Frappe
invariants once instead of re-explaining them per prompt:

- **Schema & Access** (`alfred_processing/alfred/agents/builders/schema_builder.py`) - `create_doctype`, `create_custom_field`, `create_role_with_permissions`, `create_property_setter`, `create_user_permission`.
- **Reports & Insights** (`reports_builder.py`) - `create_report`, `create_dashboard`, `create_dashboard_chart`, `create_number_card`, `create_auto_email_report`.
- **Automation & Behavior** (`automation_builder.py`) - `create_server_script`, `create_client_script`, `create_notification`, `create_workflow`, `create_webhook`, `create_auto_repeat`, `create_assignment_rule`.
- **Presentation** (`presentation_builder.py`) - `create_print_format`, `create_letter_head`, `create_email_template`, `create_web_form`, `update_print_settings`.

`doctype_builder.py` and `report_builder.py` remain as thin compat shims
that re-export the family APIs bound to their single intent, so crew.py's
dispatcher branches and any older call sites keep working.

**Registry**: `alfred/registry/intents/<intent>.json` declares shape-defining
fields (required / default / rationale). Twenty-two intent JSONs ship
today; each family owns 5-7 of them. Adding a new intent is one JSON
plus an entry in the owning family builder module (backstory fragment,
role, goal, dispatcher clause). Registry fields mirror the canonical
Frappe DocType fields with controller-enforced invariants captured in
the `rationale` strings so the specialist knows which defaults are safe
and which are load-bearing.

**Critical cross-family distinctions** the family backstories embed to
prevent the generic Developer's most common drift:

- *Property Setter vs Custom Field* (Schema): Property Setter TWEAKS
  an existing DocField's properties (reqd / label / hidden / options);
  Custom Field ADDS a new field. Emitting a Custom Field to alter an
  existing property silently creates a duplicate.
- *User Permission vs DocPerm* (Schema): User Permission is
  DOCUMENT-LEVEL per user (which records); DocPerm is DOCTYPE-LEVEL
  per role (which DocTypes). Orthogonal - both needed.
- *DocPerm `select` vs `read`* (Schema): `read` gates OPEN; `select`
  gates LIST / dropdown visibility. Granting read without select
  hides records from every list.
- *Webhook vs Server Script API* (Automation): Webhook is OUTBOUND
  (Frappe -> external URL) on a document event; Server Script API is
  INBOUND (external caller -> Frappe endpoint).
- *Assignment Rule vs Workflow* (Automation): Assignment Rule ROUTES
  documents to users via ToDos; Workflow TRACKS state on documents
  via docstatus transitions. Complementary, not substitutes.
- *Auto Repeat stale-snapshot* (Automation): generated copies reflect
  the template's CURRENT state, not setup-time state; mid-schedule
  edits propagate to future copies.
- *Print Settings singleton* (Presentation): Print Settings is a
  SINGLE DocType - the intent is `update_print_settings` with
  op=update targeting the document name 'Print Settings', not create.

**Ask, don't assume.** Every family backstory carries an explicit
contract: when a critical field is ambiguous in the prompt, the
specialist emits it as an empty string and records
`field_defaults_meta[<field>] = {"source": "needs_clarification",
"question": "..."}` rather than inventing a value. The three valid
`source` values are `"user"`, `"default"`, and `"needs_clarification"`.
Each family's backstory lists its own critical fields (target DocType,
Notification event, Workflow transitions, Web Form route, etc.)
explicitly so the model knows where defaults are inappropriate.

**Client impact**: the preview panel renders `field_defaults_meta` as
"default" / "needs clarification" pills next to each registry field,
with the rationale (or the LLM's question) surfaced as a hover tooltip.

### V2 Module specialists (`ALFRED_MODULE_SPECIALISTS`)

Module specialists are cross-cutting advisers - one Agent per ERPNext
module (Accounts, HR, Stock, ...). They are invoked twice per build:

```
... V1 path ...
    │
    ▼
classify_module → target module from prompt + target DocType
    │
    ▼
provide_module_context (PRE-PASS):
  specialist's LLM call returns a prompt snippet of conventions,
  typical roles, gotchas. Cached in Redis (falls back to in-memory,
  5-min TTL) keyed by (module, intent, target_doctype).
    │
    ▼
build_alfred_crew: specialist task description gains the module
  context as a MODULE CONTEXT section alongside the V1 checklist
    │
    ▼
crew runs → ChangesetItem
    │
    ▼
backfill_defaults_raw: V1 intent defaults + module's
  permissions_add_roles (deduped) + module's naming pattern
  (overrides intent default when intent default was applied)
    │
    ▼
module specialist validate_output (POST-PASS):
  deterministic rules (KB's validation_rules) + LLM pass, merged
  and deduped by normalised issue text. Returns a list of
  ValidationNote (severity, source, issue, field, fix).
```

**Module KBs** at `alfred/registry/modules/*.json`. 11 ship today:
accounts, custom, hr, stock, selling, buying, manufacturing, projects,
assets, crm, payroll. Adding a new module is one JSON file. Spec at
`alfred-processing/docs/specs/2026-04-22-module-specialists.md`.

**Client impact**: a **Module context** badge at the top of the preview
showing the detected module. Validation notes render in a dedicated banner
grouped by source module; blocker-severity notes from the primary module
disable the Deploy button until addressed.

### V3 Multi-module classification (`ALFRED_MULTI_MODULE`)

Cross-domain prompts get one primary + up to two secondary modules. Primary
keeps full severity; secondaries contribute context + advisory-only notes.

```
classify_module (V3 path):
  ModuleRegistry.detect_all → (primary, [secondary...])
  e.g. "Sales Invoice that auto-creates a project task"
       → primary=accounts, secondaries=[projects]
    │
    ▼
provide_module_context: fans out in parallel -
  primary specialist -> PRIMARY MODULE section
  each secondary -> SECONDARY MODULE CONTEXT section
  (failure-isolated: one specialist down doesn't block the pipeline)
    │
    ▼
backfill: primary's naming pattern wins; permissions are merged
  UNION across primary + secondaries, deduped by role
    │
    ▼
validate_output:
  primary returns notes as-is
  each secondary's notes pass through cap_secondary_severity:
    blocker -> warning (secondary modules cannot gate deploy)
```

LLM fallback for the classifier is primary-only to cap token budget.
Secondaries only come from the heuristic path. Spec at
`alfred-processing/docs/specs/2026-04-22-multi-module-classification.md`.

**Client impact**: badge extends to *"Module context: Accounts (with
Projects)"*. Validation notes group by source module; secondary groups get
an *(advisory only)* marker.

### V4 Insights → Report handoff (`ALFRED_REPORT_HANDOFF`)

Analytics prompts that used to get misrouted to Dev ("show top 10 customers
by revenue this quarter" → hallucinated Server Script) now route cleanly
to Insights, and the user can one-click-promote to a Dev-mode Report DocType
build.

```
"show top 10 customers by revenue this quarter"
    │
    ▼
classify_mode fast-path: analytics-verb pattern ("show top N", "list
  the top", "count of", "summary of") routes to insights BEFORE any
  LLM call. Explicit deploy verbs ("build a report", "create a
  report") still beat analytics and route to dev.
    │
    ▼
Insights handler → InsightsResult(reply, report_candidate):
  reply: natural-language answer
  report_candidate: heuristic extraction from the prompt -
    target_doctype, limit, time_range, suggested_name
  (None when prompt is scalar / metadata / error-reply)
    │
    ▼
ws emit insights_reply { reply, report_candidate }
    │
    ▼
Preview panel: chat bubble + [Save as Report] button when
  report_candidate present
    │
    ▼
--- user clicks [Save as Report] ---
    │
    ▼
Client sends Dev-mode prompt: human-readable header + trailing
  __report_candidate__: {json} marker
    │
    ▼
classify_intent short-circuits on marker:
  intent=create_report, source=handoff, confidence=high
  (skips heuristic + LLM classifier - no re-interpretation)
    │
    ▼
build_alfred_crew (V1 dispatch) → Report Builder specialist
    │
    ▼
Changeset preview → user approves → Report DocType deployed
```

Spec at `alfred-processing/docs/specs/2026-04-22-insights-to-report-handoff.md`.

### Flag matrix

| Flags on | Behaviour |
|---|---|
| none | Pre-V1 Alfred (generic Developer, no specialists) |
| V1 | Intent specialist dispatch + default pills in preview |
| V1 + V2 | + module context injection, module validation notes, Deploy-gating blockers |
| V1 + V2 + V3 | + primary + secondary modules, cross-domain permissions merge |
| V1 + V4 | + Insights→Report handoff button + structured `create_report` classification |

## Knowledge Architecture (three layers)

Alfred's retrievable knowledge is split across three layers. Each is authoritative
for one kind of fact; none of them duplicate each other, and platform rules never
get pasted into agent backstories any more.

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1 - Framework KG (auto-extracted schemas)                 │
│  alfred_client/data/framework_kg.json (gitignored, regenerated   │
│  at bench migrate against whatever apps are installed)           │
│                                                                  │
│  "What does the User DocType ship with?" "What fields does a     │
│  Sales Order have?" Extracted by walking every bench app's       │
│  doctype/*/*.json. Merged with live frappe.get_meta() via        │
│  lookup_doctype(name, layer="framework|site|both").              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Layer 2 - Pattern library (curated recipes)                     │
│  alfred_client/data/customization_patterns.yaml (hand-written,   │
│  committed to the repo)                                          │
│                                                                  │
│  "What does a validation Server Script look like?" Curated       │
│  templates for common customization idioms with when_to_use,     │
│  when_not_to_use, event_reasoning, template, anti_patterns.      │
│  Retrieved via lookup_pattern(query, kind).                      │
│  Starter set: approval_notification, post_approval_notification, │
│  validation_server_script, custom_field_on_existing_doctype,     │
│  audit_log_server_script, create_role_with_permissions.          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Layer 3 - Frappe Knowledge Base (FKB)                           │
│  alfred_client/data/frappe_kb/*.yaml                             │
│                                                                  │
│  "Can Server Scripts use import?" "What does frappe.db.get_value │
│  return on miss?" "How do you wire a hooks.py doc_event?"        │
│  Four kinds, each in its own file:                               │
│    rules.yaml   - sandbox constraints (8 entries)                │
│    apis.yaml    - Frappe API reference (141 entries, auto-       │
│                   scraped + 22 hand-overrides)                   │
│    idioms.yaml  - how Frappe wants it done (18 entries)          │
│    style.yaml   - Alfred code-gen preferences (10 entries)       │
│  177 entries total at the time of writing.                       │
│                                                                  │
│  Retrieved via lookup_frappe_knowledge(query, kind).             │
│  Hybrid retrieval (keyword + semantic embeddings) lives in       │
│  alfred_processing/alfred/knowledge/fkb.py so ML deps stay out   │
│  of the bench venv.                                              │
└─────────────────────────────────────────────────────────────────┘
```

The inject_kb pipeline phase (see the state-machine section) auto-pulls the
most relevant FKB entries + site customizations for the target DocType and
prepends them to the Developer task - agents don't have to know to call the
retrieval tools, although they can call them directly for depth.

The goal: keep hardcoded rules out of agent backstories. Rules drift and ship
with version numbers; data in the KGs is either regenerated against the
current site or curated by humans in YAML that's easier to review than prompt
text split across five files.

Adjacent MCP tool: `get_site_customization_detail(doctype)` returns the deep
per-DocType footprint (full workflow graphs, Server Script bodies truncated to
600 chars, custom fields, notifications) - the thing inject_kb reads for the
SITE STATE block. Peer to the shallower `get_existing_customizations` which
returns a site-wide summary.

## Conversation Memory

Per-conversation structured record persisted in Redis under
`conv-memory-<conversation_id>`:

| Field | Purpose |
|---|---|
| `items` | Doctype + name + parent (for Custom Field / Server Script) of everything proposed so far in this chat. Capped at 20. |
| `clarifications` | Q/A pairs from the clarifier that the agents should keep honouring in future turns. Capped at 10. |
| `recent_prompts` | Last 5 raw user prompts, truncated. |

Loaded at the start of `_phase_load_state`. Rendered as a short context block
that's injected into the prompt enhancer's user message so the LLM can resolve
"that DocType" -> a concrete name from the prior turn. Updated with clarifier
answers after `_phase_clarify` and with changeset items after `_phase_post_crew`.

## Dry-Run Validation

Every pipeline run validates the final changeset via the `dry_run_changeset`
MCP tool **before** the preview drawer opens. The validator classifies each
item by doctype and routes to one of two paths:

```
                    ┌──── classify doctype ────┐
                    │                          │
                    ▼                          ▼
         DDL-triggering doctype       Savepoint-safe doctype
         (DocType, Custom Field,      (Notification, Server Script,
          Property Setter, Workflow,   Client Script, Print Format,
          Workflow State, ...)         Role, Report, ...)
                    │                          │
                    ▼                          ▼
         _meta_check_only()           _savepoint_dry_run()
         - frappe.get_doc() shape      - frappe.db.savepoint("dry_run")
         - mandatory field walk        - doc.insert(ignore_permissions=True)
         - Link field target check     - frappe.db.rollback(save_point="dry_run")
         - doctype-specific semantic
           check (Workflow
           states/transitions,
           Custom Field conflict,
           etc.)
         - NEVER calls .insert() or
           .validate() - those can
           trigger DDL-cascading
           side effects
```

**Why the split?** MariaDB implicitly commits all pending DML before any DDL
statement. A savepoint rollback can't undo DDL, so calling `.insert()` on a
DocType or Custom Field would actually leave rows in the database even on
"dry-run". The controller side effects on Workflow insert (creates a
`workflow_state` Custom Field on the target doctype) cascade into ALTER TABLE,
which auto-commits the Workflow row too. `_meta_check_only` keeps the schema
surface untouched by refusing to call `.insert()` at all for any doctype in
`_DDL_TRIGGERING_DOCTYPES`.

Runtime-error checks run regardless of path:

- **Server Script**: `compile()` check catches Python syntax errors.
- **Notification**: `frappe.render_template()` on subject/message/condition
  catches Jinja syntax errors.
- **Client Script**: balanced-brace regex check.

```
Crew output → _extract_changes() → reflect_minimality → dry_run_changeset (MCP)
                                                              │
                                                              ▼
                                                  ┌── dry_run result ──┐
                                                  │                    │
                                                  ├─▶ valid=True  → Preview panel ✓
                                                  │
                                                  └─▶ valid=False → bounded retry
                                                                    once, then
                                                                    show issues +
                                                                    gate Approve

Approve click → dry_run_changeset AGAIN (belt-and-suspenders)
                      │
                      ├─▶ valid → deploy
                      │
                      └─▶ invalid (state drifted) → abort, show issues
```

Apply-time operations use `frappe.set_user(conversation.user)` + `ignore_permissions=False`
so the deploy runs with the conversation owner's permissions, not whoever
triggered the approve click. Each `create` / `update` also re-checks
`frappe.has_permission(doctype, action)` per-item before execution.

## Permission Model

Six overlapping layers. Each one is a Swiss-cheese slice; a request has to
pass all of them to touch user data.

```
Layer 1: UI access           validate_alfred_access() on page load + every
                              whitelisted endpoint. Enforces the role list in
                              Alfred Settings > Allowed Roles.

Layer 2: Endpoint ownership   Every sensitive endpoint (approve, reject, deploy,
                              rollback, start/stop/send_message, escalate)
                              calls frappe.has_permission("Alfred Conversation"
                              or "Alfred Changeset", ...) before acting. Routes
                              through the `changeset_has_permission` /
                              `conversation_has_permission` hooks which enforce
                              owner/share/System-Manager rules.

Layer 3: API transport        Shared API key + JWT signed with the same key on
                              the processing-app handshake. JWT embeds the
                              conversation owner's user + roles.

Layer 4: MCP session          _connection_manager runs as the conversation
                              OWNER (not the caller). Sets frappe.session.user
                              via frappe.set_user(owner) at start, restores in
                              a finally block. All MCP tool calls made during
                              that connection run under the owner's row-level
                              permission_query_conditions.

Layer 5: Tool-level checks    Every tool in the MCP registry uses
                              frappe.has_permission / frappe.get_meta which
                              respects the session user's roles. check_permission
                              is also exposed as a tool so agents can gate their
                              own plans.

Layer 6: Deploy-time re-check apply_changeset loops over each item and calls
                              frappe.has_permission(doctype, action) again
                              immediately before create/update, so even if
                              layers 1-5 let a request through, the write
                              itself still enforces the owner's live
                              permissions.
```

### Deploy concurrency invariant

`apply_changeset` guards against two processes trying to deploy the
same changeset with a real CAS:

```sql
UPDATE `tabAlfred Changeset`
   SET status = 'Deploying'
 WHERE name = ? AND status = 'Approved'
```

The subtle bit: just reloading the row after the UPDATE and checking
`status == 'Deploying'` is NOT enough to know we won the lock - under
a true race, process B's UPDATE matches zero rows (A already flipped
the state) but B still reads `Deploying` on reload. The real winner
check is `frappe.db._cursor.rowcount == 1` right after the UPDATE and
before the commit. Only that process proceeds; everyone else raises
"Changeset cannot be deployed - another process may have already
started this deployment."

Regression test: `alfred_client/test_cas_race.py`.

Admin-portal endpoints are on a separate trust boundary:

- **`check_plan` / `report_usage` / `register_site`**: `allow_guest=True` but
  gated by `_validate_service_key()` which checks `Authorization: Bearer
  <service_api_key>`. The processing app is the only caller that has this key.
- **`subscribe_to_plan` / `cancel_subscription`**: `@frappe.whitelist()` +
  `_require_billing_admin()` (System Manager role). Billing mutations use
  `ignore_permissions=True` internally so the role gate is the only thing
  stopping an arbitrary logged-in admin-portal user from mutating any
  customer's subscription.

## Observability

The processing app emits structured tracing spans via
`alfred/obs/tracer.py`. Enable per-process with environment variables:

| Variable | Default | Effect |
|---|---|---|
| `ALFRED_TRACING_ENABLED` | off | Master switch. `1`/`true`/`yes` to enable. |
| `ALFRED_TRACE_PATH` | `./alfred_trace.jsonl` | One JSON object per finished span, appended. |
| `ALFRED_TRACE_STDOUT` | off | Also emit a human-readable summary line to stderr. |

Spans are auto-created for every pipeline phase (`pipeline.sanitize`,
`pipeline.enhance_prompt`, `pipeline.clarify`, `pipeline.run_crew`,
`pipeline.extract`, `pipeline.rescue`, `pipeline.reflect_minimality`,
`pipeline.dry_run`, ...) and nest via `ContextVar`, so downstream analysis can
group by `trace_id` and reconstruct the conversation. Each span records
duration, parent/child relationship, arbitrary attributes (token counts, item
counts, validation results), and error status.

The tracer is intentionally zero-dep (no `opentelemetry-api` import) so a
bench deploy doesn't need extra packages. The call-site API matches the
OpenTelemetry context-manager shape, so swapping to a real OTel SDK later is
mechanical.

## Data Flow

```
Alfred Settings (config)
    │
    ├──▶ send_message()
    │         │
    │         ├──▶ Redis list (durable queue)
    │         └──▶ Redis pub/sub ("__notify__" wakeup)
    │                   │
    │         ┌─────────┘
    │         ▼
    │   Connection Manager (long queue worker, runs as conv owner)
    │         │  drains Redis list → sends over WebSocket
    │         │
    │         ├──▶ WebSocket ──▶ Processing App
    │         │                     │
    │         │                     ├─▶ AgentPipeline state machine
    │         │                     │   (tracer spans per phase)
    │         │                     │
    │         │                     ├─▶ Redis: conversation memory,
    │         │                     │   crew state, event stream
    │         │                     │
    │         │                     └─▶ Admin portal check_plan (opt)
    │         │
    │         │◀── Agent events ────┤
    │         │  (agent_status,     │
    │         │   agent_activity,   │
    │         │   minimality_review,│
    │         │   question,         │
    │         │   changeset)        │
    │         │
    │         ├──▶ frappe.publish_realtime() ──▶ Browser (Socket.IO)
    │         │
    │         ├──▶ Alfred Message (chat history)
    │         │
    │         └──▶ Alfred Changeset (proposed changes, Pending)
    │                   │
    │            (approve_changeset - write-perm gate)
    │                   │
    │                   ▼
    │            dry_run_changeset AGAIN
    │                   │
    │                   ▼
    │            apply_changeset
    │            (frappe.set_user(owner), has_permission per item)
    │                   │
    │                   ├──▶ Created DocTypes/Scripts/Workflows
    │                   ├──▶ Alfred Audit Log (every step, write-ahead)
    │                   ├──▶ Alfred Created Document (for reporting)
    │                   └──▶ Rollback Data (for undo)
    │
    └──▶ Alfred Conversation (session tracking)
         │
         └──▶ Redis conv-memory-<id>  (Phase 2 memory layer)
```

## DocType Relationships

```
Alfred Settings (Single)
    └── Alfred Allowed Role (child table)

Alfred Conversation         → owner + shared-with gate every read/write
    ├── Alfred Message (1:many via Link)
    ├── Alfred Changeset (1:many via Link)
    ├── Alfred Audit Log (1:many via Link)
    └── Alfred Created Document (child table)

Alfred Customer (Admin Portal)
    ├── Alfred Subscription (1:many via Link)
    └── Alfred Usage Log (1:many via Link)

Alfred Plan (Admin Portal)
    ├── Alfred Plan Feature (child table)
    └── pipeline_mode (Select: full | lite) - tier-locks the crew mode
                                              returned by check_plan
```

---

# Part 3 — Data Model Reference

Field-by-field reference for every Alfred DocType, plus Redis key conventions. Use this when you need to query the database or understand what a specific field stores.


Field-by-field reference for every Alfred DocType (client app + admin
portal) plus the Redis key conventions used by the processing app.

This doc is aimed at developers working on Alfred or writing reports
against Alfred's data. For the higher-level architecture, see
[how-it-works.md](how-it-works.md). For API shapes, see
[developing.md](developing.md).

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

The proposed changes from one pipeline run. Shown in the preview drawer.

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
[running.md](running.md#redis-debugging)).

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
