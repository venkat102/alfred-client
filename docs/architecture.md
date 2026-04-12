# Alfred Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Customer's Frappe Site                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐   │
│  │  Chat UI      │  │  MCP Server  │  │  Deployment Engine      │   │
│  │  /app/alfred-chat   │  │  (9 tools)   │  │  (changeset executor)   │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────────┘   │
│         │ Socket.IO        │ JSON-RPC             │                  │
│  ┌──────┴──────────────────┴──────────────────────┴──────────────┐  │
│  │              WebSocket Client (outbound)                       │  │
│  │              alfred_client app                                 │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
└─────────────────────────────┼───────────────────────────────────────┘
                              │ WSS (client-initiated)
┌─────────────────────────────┼───────────────────────────────────────┐
│                Processing App (FastAPI, native dev / Docker prod)    │
│  ┌──────────────────────────┴────────────────────────────────────┐  │
│  │              API Gateway (JWT auth + rate limiting)            │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
│  ┌──────────┐  ┌────────────┴───────────────┐  ┌────────────────┐  │
│  │  Prompt   │  │  CrewAI Orchestrator       │  │  MCP Client    │  │
│  │  Defense  │→ │  (6 agents + manager)       │←→│  (9 tools)    │  │
│  └──────────┘  └────────────┬───────────────┘  └────────────────┘  │
│                             │                                       │
│  ┌──────────────────────────┴────────────────────────────────────┐  │
│  │              Redis (state store + event streams)               │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                              │ HTTPS
┌─────────────────────────────┼───────────────────────────────────────┐
│                Admin Portal (Frappe site)                            │
│  ┌──────────────┐  ┌───────┴──────┐  ┌──────────────────────────┐  │
│  │  Customers   │  │  Usage API   │  │  Billing / Subscriptions  │  │
│  │  & Plans     │  │  (3 endpoints)│  │  (Frappe Payments)       │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
│                alfred_admin app                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Agent Pipeline - Full Mode (6 agents, sequential)

```
User Prompt
    │
    ▼ (enhance_prompt - single LLM pass)
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  Requirement │──▶│  Assessment  │──▶│  Architect   │──▶│  Developer   │
│  Analyst     │   │  Assessor    │   │  Designer    │   │  (changeset) │
└──────────────┘   └──────────────┘   └──────────────┘   └──────┬───────┘
                                                                │
                                                         ┌──────▼───────┐
                                                         │  Tester      │
                                                         │  (static)    │
                                                         └──────┬───────┘
                                                                │
                                                         ┌──────▼───────┐
                                                         │  Deployer    │
                                                         │  (plan)      │
                                                         └──────┬───────┘
                                                                │
     Pre-preview dry-run ◀───────────────────────────────────── ▼
     (via MCP dry_run_changeset)                         Crew output extract
            │
            ├─▶ valid → Preview Panel (✓ Validated - ready to deploy)
            │
            └─▶ invalid → self-heal retry (1×, Developer agent only)
                          │
                          ├─▶ retry valid → Preview Panel
                          │
                          └─▶ retry invalid → Preview Panel (issue list, Approve gated)
```

Process is `Process.sequential` (not hierarchical) - no manager agent, no delegation
loops. Agents call MCP tools synchronously during their reasoning loop; each tool
call is streamed to the UI as an `agent_activity` event so the user sees live progress.

## Agent Pipeline - Lite Mode (single agent, ~5× faster)

```
User Prompt
    │
    ▼ (enhance_prompt)
┌────────────────────────────────────────────┐
│  Alfred Lite  (role: Frappe Developer)     │
│  - Fused backstory: requirements + design  │
│    + codegen                               │
│  - max_iter=4                              │
│  - Tools: union of all specialist tools    │
│    (get_doctype_schema, check_permission,  │
│     get_existing_customizations,           │
│     dry_run_changeset, ...)                │
└───────────────────┬────────────────────────┘
                    │
                    ▼
              Crew output extract
                    │
                    ▼
           Pre-preview dry-run  (same as full mode)
                    │
                    └─▶ same preview + approve flow as full
```

Lite mode is selected when `Alfred Settings.pipeline_mode = "lite"` OR when the
admin portal's `check_plan` response includes `"pipeline_mode": "lite"`. The
plan-level override always wins. Single-agent mode trades cross-agent validation
(Assessor + Tester were the reviewers) for ~5× lower LLM cost and ~5× faster
completion, at the cost of occasional hallucinated fields on complex requests.
The pre-preview dry-run + approve-time safety net still catch insert-time
errors, so broken changesets are blocked regardless of mode.

## Permission Model (5 Layers)

```
Layer 1: UI Access       → validate_alfred_access() on page load
Layer 2: API Auth        → API key + JWT on WebSocket handshake
Layer 3: MCP Session     → frappe.set_user(conversation.user) around every MCP tool call
Layer 4: Assessment      → Deterministic permission matrix check (check_permission MCP tool)
Layer 5: Generated Perms → Tester validates generated DocType permissions
Layer 6: Deployment      → frappe.has_permission() on every operation
```

**Layer 3 is critical**: the `_connection_manager` RQ job sets the session user
at start and restores it in a `finally` block. Without this, every MCP tool call
would run as Administrator and silently bypass `permission_query_conditions`
row-level filters - a security issue that fails open, not closed.

## Dry-Run Validation

Every pipeline run validates the final changeset via the `dry_run_changeset`
MCP tool **before** the Preview Panel is shown:

```
Crew output → _extract_changes() → dry_run_changeset (MCP)
                                         │
                                         ▼
                    ┌──────── dry_run validation ────────┐
                    │                                    │
                    │  1. DocType exists?                │
                    │  2. Operation valid (create/update)│
                    │  3. No naming conflict             │
                    │  4. Runtime checks:                │
                    │     - Python compile for Server    │
                    │       Scripts                      │
                    │     - Jinja render for Notification│
                    │       subject/message/condition    │
                    │     - Balanced braces for Client   │
                    │       Scripts                      │
                    │  5. Savepoint insert + rollback    │
                    │     (catches mandatory field,      │
                    │      link target errors)           │
                    │                                    │
                    └─────────────┬──────────────────────┘
                                  │
                                  ├─▶ valid=True  → Preview panel shows ✓
                                  │
                                  └─▶ valid=False → bounded retry once,
                                                    then show issues + gate Approve

Approve click → dry_run_changeset AGAIN (belt-and-suspenders)
                    │
                    ├─▶ valid → deploy
                    │
                    └─▶ invalid (state drifted) → abort, show issues
```

The second dry-run at approve time catches DB drift between preview and deploy
(e.g., another user added a conflicting DocType in the interim). If the two
dry-runs disagree, `approve_changeset` logs a warning.

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
    │   Connection Manager (long queue worker)
    │         │  drains Redis list → sends over WebSocket
    │         │
    │         ├──▶ WebSocket ──▶ Processing App
    │         │                     │
    │         │◀── Agent events ────┘
    │         │
    │         ├──▶ frappe.publish_realtime() ──▶ Browser (Socket.IO)
    │         │
    │         ├──▶ Alfred Message (chat history)
    │         │
    │         └──▶ Alfred Changeset (proposed changes)
    │                   │
    │            (user approval)
    │                   │
    │                   ▼
    │            Deployment Engine
    │                   │
    │                   ├──▶ Created DocTypes/Scripts/Workflows
    │                   ├──▶ Alfred Audit Log (every step)
    │                   └──▶ Rollback Data (for undo)
    │
    └──▶ Alfred Conversation (session tracking)
```

## DocType Relationships

```
Alfred Settings (Single)
    └── Alfred Allowed Role (child table)

Alfred Conversation
    ├── Alfred Message (1:many via Link)
    ├── Alfred Changeset (1:many via Link)
    ├── Alfred Audit Log (1:many via Link)
    └── Alfred Created Document (child table)

Alfred Customer (Admin Portal)
    ├── Alfred Subscription (1:many via Link)
    └── Alfred Usage Log (1:many via Link)

Alfred Plan (Admin Portal)
    └── Alfred Plan Feature (child table)
```
