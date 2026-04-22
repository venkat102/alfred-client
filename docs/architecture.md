# Alfred Architecture

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
│             │   before the crew runs. Doubles as a health probe:
│             │   any failure (connection refused, timeout, HTTP 500
│             │   from a dead model runner) stops the pipeline with
│             │   OLLAMA_UNHEALTHY rather than letting the crew burn
│             │   2-3 minutes of retries per agent. Cloud providers
│             │   (no ollama/ prefix) are skipped.
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
│classify_    │   dev-mode prompt into a known intent (create_doctype,
│intent       │   create_report, ...) or "unknown". Two paths:
│             │   (a) Handoff short-circuit: if the prompt carries a
│             │       __report_candidate__ JSON trailer (user clicked
│             │       "Save as Report" on an Insights reply), force-
│             │       classify intent=create_report, source=handoff,
│             │       no LLM call. ctx.report_candidate carries the
│             │       structured spec for downstream phases.
│             │   (b) Normal path: heuristic pattern matcher first
│             │       (e.g. "create a doctype" -> create_doctype;
│             │       "save as report" -> create_report), LLM fallback
│             │       constrained to the supported intent list.
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
│             │   Developer is swapped for that specialist
│             │   (DocType Builder for create_doctype, Report Builder
│             │   for create_report) and the generate_changeset task
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
classify_intent → "create_doctype" | "create_report" | "unknown"
    │
    ▼
build_alfred_crew:
  if intent has specialist:
      agents["developer"] = build_<intent>_builder_agent(...)
      task description += render_registry_checklist(intent_schema)
  else:
      agents["developer"] = generic Developer (pre-V1)
    │
    ▼
crew runs → ChangesetItem
    │
    ▼
backfill_defaults_raw: intent-registry defaults layered with
  field_defaults_meta tagging source=user|default per field
```

**Registry**: `alfred/registry/intents/<intent>.json` declares shape-defining
fields (required / default / rationale). Two intents ship today:
`create_doctype` (module, is_submittable, autoname, istable, issingle,
permissions) and `create_report` (ref_doctype, report_type, is_standard,
module). Adding a new intent is one JSON file plus a builder module.

**Client impact**: the preview panel renders `field_defaults_meta` as
"default" pills next to each registry field, with the rationale surfaced
as a hover tooltip.

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
