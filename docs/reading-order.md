# Reading Order

A recommended path through the Alfred documentation. Follow this if you
want to understand the whole system end-to-end - from "what is this?" to
"I can add a new pipeline phase".

Everything here assumes you want full coverage. If you have a specific
goal (install Alfred / debug an incident / write a security review),
skip to the [goal-based shortcuts](#goal-based-shortcuts) at the bottom.

## Total reading time

| Level | Duration | Goal |
|---|---|---|
| Minimum viable understanding | **~45 minutes** | You know what Alfred does and can talk about it |
| Developer-ready | **~2.5 hours** | You can contribute code without needing a mentor |
| Operator-ready | **~3 hours** | You can run Alfred in production and handle incidents |
| Full mastery | **~5 hours** | You know every corner including benchmarks + data model |

These times assume focused reading with occasional glances at referenced
code. Double them if you want to cross-reference every mention with the
actual source, which you should do at least once.

---

## Phase 1 - Orientation (~15 minutes)

**Goal**: understand what Alfred is, what problem it solves, and how the
pieces fit together at a high level.

### 1. [README.md (client app)](../README.md) - 3 minutes

One-screen overview of Alfred's key features. Don't try to understand
everything here - just get a sense of the surface area (full / lite
pipeline, MCP tools, dry-run, preview, rollback, 6-layer permissions).

**What to focus on**: the **Key Features** bullet list. The
**Documentation** table tells you where each topic is covered - bookmark
it but don't click through yet.

**What to skip**: the quick start block (come back to it in Phase 4).

### 2. [alfred_processing/README.md](../../../../alfred_processing/README.md) - 3 minutes

The processing-app side of the story. Mostly a component checklist -
state machine + 6-agent crew + MCP client + handoff condenser +
conversation memory + reflection + tracing + dry-run.

**Takeaway**: Alfred spans two processes (client app on Frappe, processing
app standalone). Get a rough sense of what lives where.

### 3. [how-alfred-works.md, sections 1-2](how-alfred-works.md) - 10 minutes

Stop here for now - don't read the whole `how-alfred-works.md` yet. Just
the first two sections:

- **The 30-second pitch** - the elevator summary
- **Components and responsibilities** - the three-box diagram +
  "why three processes?" explanation

**At the end of Phase 1** you should be able to answer:
- What problem does Alfred solve?
- What are the three processes and what does each do?
- What's MCP and why does it exist?
- What's the difference between "full" and "lite" mode?

If you can't, re-read the phase.

---

## Phase 2 - Functional understanding (~30 minutes)

**Goal**: understand Alfred from the user's point of view. You should be
able to describe what a user sees at every point in a conversation.

### 4. [user-guide.md](user-guide.md) - 20 minutes

The most important functional doc. Walks through the chat UI, the
preview panel, the approval flow, the status badges, the error messages,
and rollback. Covers the Basic mode badge (lite pipeline selection),
the live activity ticker, and the conversation list.

**What to focus on**: "Your First Conversation" (step-by-step), "When
Things Go Wrong" (error catalogue), and "The Interface" (preview panel
layout).

**What to skip on first pass**: the detailed preview-panel section for
every DocType (Workflow states / Server Script body / Custom Field
options). Skim it - you'll internalise the details when you first see
them in a live run.

### 5. [SETUP.md Quick Start only](SETUP.md) - 5 minutes

Just the 5-minute Quick Start block at the top. Don't read the full
document yet - it's 1189 lines and 90% of it is reference material you
don't need on a first pass.

**What to focus on**: the list of services that need to be running
(Frappe + processing app + Redis + LLM). This tells you what's being
installed when you hit the quick-start commands.

### 6. [how-alfred-works.md, section 3 "Three kinds of knowledge"](how-alfred-works.md#three-kinds-of-knowledge) - 5 minutes

How Alfred retrieves Frappe facts. Three layers: the framework KG (DocType
schemas, auto-extracted), the pattern library (hand-curated recipes), and
the Frappe Knowledge Base (platform rules, API reference, idioms, house
style). A pipeline phase auto-injects the most relevant entries into the
Developer task so agents don't have to know to ask for them. Read this
before the pipeline walkthrough so the "FRAPPE KB CONTEXT" banner in the
Developer task makes sense.

### 7. [how-alfred-works.md, section 4 "Chat modes and the orchestrator"](how-alfred-works.md#chat-modes-and-the-orchestrator) - 5 minutes

The three-mode chat section. Not every message is a build request - some
are conversational ("hi", "thanks"), some are read-only queries ("what
DocTypes do I have?"), some are plan-before-build design questions. The
orchestrator classifies each prompt and routes it to Dev / Plan /
Insights / Chat mode. Read this before the full-pipeline walkthrough so
you understand which mode the example is demonstrating.

### 8. [how-alfred-works.md, section 5](how-alfred-works.md#example-prompt-end-to-end) - 5 minutes

The "Example prompt, end to end" section. It walks through a concrete
user flow (sending a prompt about expense claim notifications) and
narrates what happens at each step. This is Dev mode specifically.

**On this first pass**, just read the user-visible events at each step.
Ignore the code blocks. You're building a functional model here, not a
technical one.

**At the end of Phase 2** you should be able to answer:
- What does a user do to get Alfred to build them something?
- What four modes exist and how does the orchestrator pick between them?
- What does Alfred show the user before it deploys anything?
- What happens if Alfred gets it wrong on the first try?
- What happens after deploy?
- Where does the user see errors?

---

## Phase 3 - Technical foundation (~45 minutes)

**Goal**: understand the technical architecture. You should be able to
draw the system from memory and explain why it's shaped this way.

### 7. [how-alfred-works.md (full read)](how-alfred-works.md) - 25 minutes

Now go back and read `how-alfred-works.md` start to finish, **including
the code blocks**. Pay attention to:

- The **9 pipeline phases** (steps 4-12 of the example walkthrough) and
  what each one does.
- **What happens in the Developer agent's head** - the ReAct loop, the
  MCP round-trip, the Phase 1 tracking state.
- **Dry-run: the safety net** - the DDL vs savepoint split. This is
  non-obvious and critical for understanding why Alfred's safety model
  works.
- **How the safety layers stack up** - the 14-row table. Count them.
- **Why the design looks like this** - the 5 design questions.

This section is 1500 lines but it's written as a narrative, so it reads
quickly. Don't skip anything.

### 8. [architecture.md](architecture.md) - 20 minutes

Now read the reference doc. Some of this will feel repetitive after
`how-alfred-works.md` - that's fine, the repetition reinforces the
mental model.

**What to focus on**:
- The **System Overview** ASCII diagram (you should already know most
  of this, confirm you understand every arrow)
- **Pipeline State Machine** section - the phase list + how
  `AgentPipeline.run()` orchestrates them
- **Crew Modes** (full vs lite) - the handoff condenser is called out
  here
- **Framework Knowledge Graph** - the KG extractor + curated pattern
  library
- **Conversation Memory** - the per-conversation persistence layer
- **Dry-Run Validation** - the decision tree
- **Permission Model** - the 6-layer table (read this carefully)
- **Data Flow** diagram
- **DocType Relationships**

Don't read sections you already understand from the narrative doc.
This is reference-mode reading.

**At the end of Phase 3** you should be able to answer:
- Walk through what happens from the moment a user hits Send to the
  moment the changeset preview appears.
- Why is the MCP session run as the conversation owner, not the caller?
- Why does `dry_run_changeset` have two paths (savepoint + meta-only)?
- What are the 9 pipeline phases and what does each do?
- What's conversation memory and why does it matter?
- How does the handoff condenser reduce token cost?

---

## Phase 4 - Implementation depth (~1 hour)

**Goal**: understand the internal APIs + data model + debugging surface.
You should be able to contribute code, fix bugs, and understand any log
line.

### 9. [developer-api.md](developer-api.md) - 25 minutes

The canonical API reference. Covers the REST API, WebSocket protocol,
12 MCP tools, pipeline state machine internals, reflection, tracing,
conversation memory, admin portal API, Alfred Plan fields.

**What to focus on**:
- **MCP Tools Reference** - the 12-tool table + the detailed sections
  on `dry_run_changeset`, `lookup_doctype`, `lookup_pattern`
- **Pipeline State Machine** - the `PipelineContext` field reference
  and the `PHASES` list
- **Handoff Condenser** - how the `Task.callback` trick works
- **Conversation Memory** - API + bounded fields
- **Tracing** - environment variables + the JSONL format + jq recipes
- **Admin Portal API** - `check_plan` response shape with
  `pipeline_mode`
- **Adding New Pipeline Phases** / **Adding New Agents** / **Adding New
  MCP Tools** - these are the "recipes" you'll actually use when
  contributing

**What to skip**: "Agent Output Schemas" (these are currently
disconnected reference - they're the Pydantic models that aren't wired
via CrewAI's `output_json` because Ollama wraps JSON in code fences).

### 10. [data-model.md](data-model.md) - 15 minutes

Field-by-field reference for every DocType on both the client app and
the admin portal, plus the Redis key conventions.

**What to focus on**:
- **Alfred Conversation** - owner, status enum, child table relationships
- **Alfred Changeset** - the status lifecycle diagram
  (Pending → Approved → Deploying → Deployed / Failed / Rolled Back)
- **Alfred Settings** - the full tab reference (Connection / LLM /
  Access Control / Limits)
- **Alfred Plan** - with the `pipeline_mode` field (tier-locked mode
  returned by `check_plan`)
- **Redis key conventions** - the namespace prefix pattern + the
  distinction between processing-app Redis and Frappe's three Redis
  instances

Skim everything else unless you're working on that specific area.

### 11. [debugging.md](debugging.md) - 20 minutes

The developer debugging reference. Covers:

- Which Redis instance does what (port 13000 vs 11000 vs 12000)
- How to tail worker logs, grep for specific markers
- Common pitfalls table (wrong Redis, missing key prefix, no long
  worker, etc.)
- **Pipeline Tracing** - how to enable `ALFRED_TRACING_ENABLED` + jq
  recipes for analysing the JSONL
- **Reflection drop events** - log markers + UI events
- **Conversation Memory Inspection** - how to read the Redis payload

**What to focus on**: the Common Pitfalls table (it's the most
actionable part) and the tracing section (you'll use it when you need
to debug a slow pipeline).

**At the end of Phase 4** you should be able to:
- Add a new phase to the pipeline (know which file, which list, which
  test).
- Add a new MCP tool (know the 7-step recipe).
- Enable tracing on a dev server and analyse the JSONL.
- Read any log line and know what component it came from.
- Look up a stale conversation's memory in Redis.
- Identify which DocType field stores any piece of persistent state.

---

## Phase 5 - Production readiness (~45 minutes)

**Goal**: understand the operator + security surface. You should be able
to deploy Alfred to production, handle incidents, and respond to a
security review.

### 12. [SETUP.md (full read, not just Quick Start)](SETUP.md) - 20 minutes

Now read the full setup doc including the sections you skipped in
Phase 2. Key areas:

- **Part A-F** - the ordered installation walkthrough
- **Configuration Reference** - every Alfred Settings field + every env
  var
- **LLM Provider Configuration** - Ollama local/remote, Claude, GPT,
  Gemini, Bedrock (read the one you'll actually use)
- **Production Deployment**
- **Backup & Recovery**
- **Monitoring**

You can skim the cloud-provider sections you don't care about, but
read the production deployment + backup sections in full.

### 13. [admin-guide.md](admin-guide.md) - 5 minutes

Short reference for the per-site Alfred Settings + the admin portal +
the processing app env vars. After SETUP.md this is almost entirely
review, but the **env var tables** (Core / Feature flags) are the
canonical list for ops config.

### 14. [operations.md](operations.md) - 15 minutes

The operator runbook. Service inventory, restart procedures, key
rotation, 8 incident response runbooks, scheduled maintenance, disaster
recovery, metrics to monitor.

**What to focus on**:
- **Service inventory** - what should be running and how to check
- **Restart the processing app** / **Restart the Frappe site** -
  procedures with gotchas
- **Rotate the API secret key** - the multi-step coordinated rotation
- **Incident response** - read all 8 scenarios. At least one will save
  you in the first month.
- **Metrics to monitor** - what to alert on externally

### 15. [SECURITY.md](SECURITY.md) - 15 minutes

The threat model + authorization documentation. By this point the
content will feel familiar (you already know the 6-layer permission
model from architecture.md and how-alfred-works.md), but read it
anyway because:

- The **Known risks and mitigations** section covers attack vectors
  you haven't seen yet (prompt injection, secrets in prompts, LLM
  provider retention, SQL injection via agent-generated scripts).
- The **Production security checklist** is a pre-flight you'll use
  every time you deploy.
- The **Reporting a vulnerability** section establishes the disclosure
  process.

**At the end of Phase 5** you should be able to:
- Deploy Alfred to a new production environment following a checklist.
- Respond to any of the 8 incident scenarios in operations.md.
- Rotate any credential without downtime.
- Explain Alfred's security model to an auditor.
- Identify every environment variable and what it controls.

---

## Phase 6 - Specialist topics (~45 minutes)

**Goal**: areas you only need if you're working on specific things.
Skip any of these that aren't relevant to your role.

### 16. [benchmarking.md](benchmarking.md) - 15 minutes

Only read this if you're optimizing pipeline performance, validating a
change, or setting up CI gates against regressions. Covers:

- The fixed 6-prompt benchmark suite
- What's mocked vs what's real
- Running the harness + reading the JSON output
- Comparing two runs with the regression gate
- Extending the prompt set

**Skip if**: you're not planning to modify the pipeline itself.

### 17. [self-hosted-guide.md](self-hosted-guide.md) - 5 minutes

A 100-line quick-start for running the processing app yourself. By
this point it's almost entirely a subset of SETUP.md's Part B - just
skim it for the Docker-specific notes.

**Skip if**: you're not self-hosting.

### 18. [CHANGELOG.md](../../../../alfred_processing/CHANGELOG.md) - 20 minutes

The phase-by-phase history. Read this last because it requires the
most context - you need to understand what Phase 1 / Phase 2 / Phase 3
*did* to appreciate the changelog entries.

**What to focus on**:
- The three phase sections (Phase 1 / Phase 2 / Phase 3) - each one
  covers a specific optimization theme.
- The **Fixed** and **Security** sections under Unreleased - these are
  the bugs the security audit caught and the DDL dry-run fix.
- The **Pre-Phase-1 foundation** summary at the bottom to understand
  what was already in place before the phased work started.

**Why this is valuable**: it tells you what has been *tried* and what
worked. A lot of the "why" in the codebase makes sense only after you
know the history.

### 19. [BENCH_CUSTOMIZATIONS.md](BENCH_CUSTOMIZATIONS.md) - 2 minutes

Two-line reference doc for the Procfile `worker_long` entry. Read it
once, then forget about it unless you ever set up a new bench.

**At the end of Phase 6** you've read every doc in the project. You
have a complete working model of the system.

---

## The canonical reading order, condensed

For reference, the full order in one list:

1. `alfred_client/README.md` (3 min)
2. `alfred_processing/README.md` (3 min)
3. `how-alfred-works.md` sections 1-2 only (10 min)
4. `user-guide.md` (20 min)
5. `SETUP.md` Quick Start only (5 min)
6. `how-alfred-works.md` section 3 (5 min)
7. `how-alfred-works.md` full read (25 min)
8. `architecture.md` (20 min)
9. `developer-api.md` (25 min)
10. `data-model.md` (15 min)
11. `debugging.md` (20 min)
12. `SETUP.md` full read (20 min)
13. `admin-guide.md` (5 min)
14. `operations.md` (15 min)
15. `SECURITY.md` (15 min)
16. `benchmarking.md` (15 min) - optional
17. `self-hosted-guide.md` (5 min) - optional
18. `CHANGELOG.md` (20 min)
19. `BENCH_CUSTOMIZATIONS.md` (2 min)

Total: ~4.5 hours including the optional items.

---

## Goal-based shortcuts

If you don't need full coverage, use these shortcuts. Each assumes you
already have basic context (Phase 1 only, ~15 min).

### "I just want to use Alfred"

- `user-guide.md` - that's it. Everything you need is in there.
- **Time**: 20 min.

### "I'm installing Alfred on a new site"

1. `SETUP.md` (full read)
2. `admin-guide.md`
3. `debugging.md` Common Pitfalls section only

**Time**: 35 min. Skip everything else until you actually hit a problem
you can't solve.

### "I'm debugging a production issue"

1. `operations.md` - start here, pick the matching incident scenario
2. `debugging.md` - for detailed log commands
3. `how-alfred-works.md` section 3 (the end-to-end walkthrough) - to
   re-orient when you've lost track of the flow

**Time**: 30 min if you're lucky, 2+ hours if you're not.

### "I'm contributing code"

1. `how-alfred-works.md` (full read) - mental model first
2. `architecture.md` - component reference
3. `developer-api.md` - the "Adding New ..." recipes at the bottom
4. `data-model.md` - if your change touches DocTypes
5. `benchmarking.md` - to gate your change against regressions
6. `CHANGELOG.md` - to see what's been tried before

**Time**: 2-3 hours. Worth every minute.

### "I'm writing a security review"

1. `SECURITY.md` - the full threat model
2. `how-alfred-works.md` section 9 ("How the safety layers stack up")
3. `architecture.md` Permission Model section
4. `CHANGELOG.md` Unreleased Security section - for the recent audit
   fixes
5. `operations.md` key rotation sections

**Time**: 1 hour.

### "I'm evaluating Alfred for my organisation"

1. `README.md` (client app) - Key Features
2. `how-alfred-works.md` - full read (this is the pitch doc)
3. `SECURITY.md` - the trust boundaries + production checklist
4. `SETUP.md` - System Requirements + LLM Provider Configuration

**Time**: 1 hour. You'll know everything you need to make a go/no-go
decision.

### "I want to optimize the pipeline"

1. `how-alfred-works.md` sections 3-5 (the pipeline walkthrough + the
   Developer agent section + dry-run)
2. `architecture.md` - Pipeline State Machine + Crew Modes
3. `benchmarking.md` - full read
4. `CHANGELOG.md` Phase 1 + Phase 2 + Phase 3 - every phase was an
   optimization, read them to see what's been tried
5. `debugging.md` Pipeline Tracing section

**Time**: 2 hours.

---

## How to use this doc

**If you're following the full reading order**, treat each phase as a
stopping point. At the end of each phase, do the self-test (the "At the
end of Phase N you should be able to..." bullets). If you can't answer
one, go back and re-read the relevant section before moving on.

**If you're using a goal-based shortcut**, know that you'll have gaps.
When you hit something you don't understand, jump to the relevant doc
from the full ordered list. The ordering above reflects the dependency
chain - each doc assumes you've read the earlier ones.

**Don't read all 19 docs in one sitting.** Your retention drops after
~2 hours. The full path is designed to be split across 3-4 sessions
over a few days.

**Do take notes.** Especially in Phase 3 (technical foundation) and
Phase 4 (implementation depth). Alfred has a lot of moving parts and
the only way to keep them straight is to actively rebuild the mental
model in your own notes. If you can draw the system from memory after
a week, you've internalised it.
