# How Alfred Works

This document is the **cross-cutting explanation** of Alfred's architecture.
Every other doc in `docs/` is reference material organised by topic
(architecture, API, security, operations, data model). This one is a
**narrative tour**: it follows one concrete example prompt from the moment
a user types it to the moment Alfred's change is deployed and audited,
and at each step explains both what the user sees and what the code does.

Read this doc once, end-to-end, to get a real mental model of the system.
Read the other docs when you need specific details.

> **Not sure where to start?** See [reading-order.md](reading-order.md)
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
customization shapes: approval_notification, validation_server_script,
custom_field_on_existing_doctype. Each has when_to_use / when_not_to_use /
template / anti_patterns. Agents query via `lookup_pattern(name, kind)`.

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
section of architecture.md](architecture.md#permission-model).

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

See [architecture.md dry-run section](architecture.md#dry-run-validation)
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

- [architecture.md](architecture.md) - system diagrams + component
  responsibilities
- [developer-api.md](developer-api.md) - full API reference + internal
  module contracts
- [data-model.md](data-model.md) - DocType field references + Redis
  key conventions

**If you're debugging a problem:**

- [debugging.md](debugging.md) - log markers, Redis commands, common
  pitfalls, pipeline tracing
- [operations.md](operations.md) - incident response runbooks, how to
  rotate keys, how to drain stuck queues

**If you're evaluating Alfred for production:**

- [SECURITY.md](SECURITY.md) - trust boundaries, authentication, data
  handling, production checklist
- [operations.md](operations.md) - service inventory, metrics to monitor,
  disaster recovery
- [SETUP.md](SETUP.md) - installation, cloud LLM providers, production
  deployment

**If you're optimizing performance:**

- [benchmarking.md](benchmarking.md) - running the benchmark harness,
  reading the JSON output, gate thresholds

**If you're a user who just wants to use it:**

- [user-guide.md](user-guide.md) - chat UI walkthrough, preview drawer,
  tips for writing better prompts
- [SETUP.md](SETUP.md) - quick start

**If you're a site admin configuring Alfred:**

- [SETUP.md](SETUP.md) - installation + connection setup
- [admin-guide.md](admin-guide.md) - Alfred Settings tabs, processing
  app env vars, admin portal fields
