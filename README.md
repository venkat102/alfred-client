# Alfred

AI-powered assistant that builds Frappe/ERPNext customizations through conversation. Describe what you need - a DocType, workflow, script, or report - and Alfred designs, generates, validates, and deploys it to your site.

## Key Features

- **Three-mode chat** (feature-flagged via `ALFRED_ORCHESTRATOR_ENABLED=1` on the processing app) - Not every prompt is a build request. An orchestrator classifies each prompt into **Dev** (build and deploy via the 6-agent crew), **Plan** (produce a reviewable plan document via a 3-agent planning crew, no DB writes), **Insights** (read-only Q&A about your current site state, 5-call MCP tool budget, no DB writes), or **Chat** (conversational reply, no crew). Users can force a specific mode via the header switcher; Auto lets the orchestrator decide. Plan mode supports Plan → Dev handoff: clicking *Approve & Build* on a plan doc promotes it into a Dev run with the plan injected as an explicit spec. See [how-alfred-works.md](docs/how-alfred-works.md#chat-modes-and-the-orchestrator).
- **Per-intent Builder specialists** (feature-flagged via `ALFRED_PER_INTENT_BUILDERS=1`) - Dev mode's generic Developer agent is swapped for a specialist based on the classified intent. Two specialists ship today: **DocType Builder** (`create_doctype`) and **Report Builder** (`create_report`). Each specialist carries a domain-focused backstory plus a registry-driven checklist of shape-defining fields. Missing or defaulted fields surface in the preview panel as editable "default" pills with rationale tooltips, so the user sees exactly which choices Alfred made vs. which came from the prompt.
- **Module specialists** (feature-flagged via `ALFRED_MODULE_SPECIALISTS=1`, requires per-intent builders) - cross-cutting domain advisers that inject ERPNext-module-specific conventions into the specialist's prompt before build and validate the output against module conventions after. 11 modules ship today: Accounts, Custom, HR, Stock, Selling, Buying, Manufacturing, Projects, Assets, CRM, Payroll. The preview panel shows a **Module context** badge and a distinct notes section grouped by source module - advisory / warning / blocker severity. Blocker-severity notes from the primary module disable the Deploy button until addressed; warnings and advisories surface but don't gate.
- **Multi-module classification** (feature-flagged via `ALFRED_MULTI_MODULE=1`, requires module specialists) - detects a primary + up to 2 secondary modules for cross-domain prompts. *"Sales Invoice that auto-creates a Project task"* classifies as primary=Accounts, secondary=[Projects] and merges both modules' permissions (deduped) into the output. Secondary-module blockers are capped to warning so only primary concerns gate deploy. Badge renders as *"Module context: Accounts (with Projects)"*; validation notes render grouped by source module with *(secondary - advisory only)* markers on the secondary groups.
- **Insights → Report handoff** (feature-flagged via `ALFRED_REPORT_HANDOFF=1`) - when you ask an analytics question (*"show top 10 customers by revenue this quarter"*, *"list the top 5 suppliers"*, *"count of invoices this month"*) Alfred routes to Insights instead of Dev, answers inline, and - if the query is report-shaped - attaches a **Save as Report** button. Clicking it fires a Dev-mode turn with a structured handoff that bypasses re-interpretation: the pipeline short-circuits intent classification to `create_report` and the Report Builder specialist emits a Report DocType changeset you can review and deploy.
- **Full & Lite pipeline modes** - Within Dev mode, Full runs a 6-agent SDLC pipeline (highest quality, ~5-10 min). Lite runs a single-agent fast pass (~1 min, ~5x cheaper) for simple customizations. Set per-site via Alfred Settings or tier-locked via the admin portal's `Alfred Plan.pipeline_mode` field.
- **Live MCP tool calls (16 tools)** - Agents query the actual Frappe site (DocType schemas, permissions, existing customizations) during reasoning, not hardcoded snapshots. Every tool call streams to the UI as concrete progress ("Reading Leave Application schema...", "Checking write permission on Notification..."). Includes consolidated `lookup_doctype` (framework KG + live site merged view), `lookup_pattern` (5 starter customization idioms with templates), and `lookup_frappe_knowledge` (Frappe Knowledge Base: platform rules, APIs, idioms). Source of truth: `TOOL_REGISTRY` in `alfred_client/mcp/tools.py` - if you add a tool there, update this count.
- **Framework Knowledge Graph** - At `bench migrate`, Alfred extracts metadata from every installed bench app's DocType JSONs and writes it to `framework_kg.json`. Agents query the KG via `lookup_doctype(layer="framework")` for authoritative mandatory-field lists without hitting the live meta cache. Rebuild on every migrate so the KG tracks the apps installed on the specific site.
- **Conversation memory** - Alfred remembers what it built earlier in a chat. "Now add a description field to that DocType" resolves against the previous turn's items + clarifications without you respelling the DocType name. Memory is per-conversation, bounded, and persisted in Redis.
- **Minimality reflection** (feature-flagged) - Optional post-crew step that trims items the user didn't ask for. Catches overreach (user asked for a notification but got notification + audit log + custom field). Enable with `ALFRED_REFLECTION_ENABLED=1` on the processing app.
- **Pre-preview dry-run validation** - Every changeset is validated against your live database **before** you see the preview, so you only review deployable solutions. DDL-triggering doctypes (DocType, Custom Field, Workflow, Property Setter) route through a meta-only path that never calls `.insert()` to avoid MariaDB implicit-commit-on-DDL leakage. Savepoint-safe doctypes use savepoint + rollback. Includes Python syntax checks on Server Scripts and Jinja checks on Notifications. Failed dry-runs trigger one automatic self-heal retry.
- **Approve-time safety net** - A second dry-run runs on Approve click to catch DB drift between preview and deploy.
- **Rich changeset preview** - The Preview Panel renders full Workflow states + transitions, Notification event/recipients/Jinja, Server Script body with syntax highlighting, Custom Field target + type + options, DocType field tables, and a validation banner. Not a JSON dump - a review-ready summary of every document that would be deployed.
- **Permission-aware at six layers** - UI role gate, endpoint ownership check, JWT on transport, MCP session user = conversation owner, per-tool `has_permission` check, per-item re-check at deploy time. An Alfred-role user can't read, approve, deploy, or rollback another user's changeset.
- **Pipeline tracing** (opt-in) - Enable `ALFRED_TRACING_ENABLED=1` on the processing app to emit one JSONL span per pipeline phase (duration, token counts, tool calls, errors). Zero-dep tracer with an OpenTelemetry-compatible call-site API so swapping to a real OTel SDK is a one-file change.

## Documentation

**New here?** Follow the **[Reading Order](docs/reading-order.md)** — a structured path through every doc in the right order, with time estimates and self-tests at each phase. ~45 minutes to minimum-viable understanding, ~4.5 hours to full coverage.

**Want the narrative first?** Read **[How Alfred Works](docs/how-alfred-works.md)** — a 30-minute end-to-end walkthrough that ties the functional user journey to the technical implementation.

| Document | Who It's For | What It Covers |
|----------|-------------|---------------|
| **[Reading Order](docs/reading-order.md)** | Anyone onboarding | Recommended sequence through every doc, grouped into 6 phases (orientation → functional → technical → implementation → operations → specialist). Time estimates, self-tests per phase, goal-based shortcuts. |
| **[How Alfred Works](docs/how-alfred-works.md)** | Everyone | End-to-end narrative walkthrough. One prompt from Send click to deployed + audited, showing user-visible events AND technical internals side-by-side. Covers first prompt, multi-turn follow-up, approval + deploy, rollback, failure paths, and why the design choices were made. |
| **[Setup Guide](docs/SETUP.md)** | Admins / DevOps | Installation, configuration, Docker setup, cloud LLM providers, production deployment, backup, monitoring |
| **[User Guide](docs/user-guide.md)** | End Users | How to use the chat, what each screen shows, conversation walkthrough, error handling, rollback, tips |
| **[Admin Guide](docs/admin-guide.md)** | Site Admins | Alfred Settings reference, admin portal config, processing app env vars |
| **[Operations Runbook](docs/operations.md)** | Operators | Service inventory, restart procedures, incident response, key rotation, monitoring checklist |
| **[Security Model](docs/SECURITY.md)** | Security reviewers / auditors | Trust boundaries, 6-layer permission model, data handling, known risks, production checklist |
| **[Architecture](docs/architecture.md)** | Developers | System design, agent pipeline, state machine, permission model, Framework KG, data flow |
| **[API Reference](docs/developer-api.md)** | Developers | REST API, WebSocket protocol, 16 MCP tools (full set; the Insights mode subset is 14), reflection, tracing, state machine internals |
| **[Data Model](docs/data-model.md)** | Developers | Field-by-field reference for every Alfred DocType + Redis key conventions |
| **[Debugging Guide](docs/debugging.md)** | Developers | Message flow, step-by-step verification, pipeline tracing, common problems |
| **[Benchmarking Guide](docs/benchmarking.md)** | Developers | Running the benchmark harness, reading the JSON output, gate thresholds, extending the prompt set |
| **[Frontend Tests](frontend-tests/README.md)** | Developers | Playwright UI smoke tests (send prompt, preview, approve, rollback). Gated LLM specs under `ALFRED_RUN_SLOW_TESTS=1`. |
| **[Self-Hosted Guide](docs/self-hosted-guide.md)** | Self-hosters | Quick-start for running your own processing app + Ollama |

**Reading paths:**
- **New engineer onboarding?** [How Alfred Works](docs/how-alfred-works.md) → [Architecture](docs/architecture.md) → [API Reference](docs/developer-api.md)
- **Installing Alfred?** [Setup Guide](docs/SETUP.md) → [Admin Guide](docs/admin-guide.md)
- **Something broken?** [Debugging Guide](docs/debugging.md) → [Operations Runbook](docs/operations.md)
- **Production deploy?** [Security Model](docs/SECURITY.md) + [Operations Runbook](docs/operations.md)
- **Just want to chat?** [User Guide](docs/user-guide.md)

## Quick Start

```bash
# Install on your Frappe site
bench get-app https://github.com/your-org/alfred_client.git
bench --site your-site install-app alfred_client
bench --site your-site migrate
bench build --app alfred_client
```

Then set up the [Processing App](docs/SETUP.md#part-b-set-up-the-processing-app) and [configure Alfred Settings](docs/SETUP.md#part-c-connect-them-together).

## Development

### CI + pre-commit

- GitHub Actions runs `ruff check` on every PR and push to `master`.
  This repo is lint-only in CI because the Python tests are
  bench-executed (they need a full Frappe + MariaDB + Redis site),
  which is out of scope for Actions. Integration tests live in
  `frontend-tests/` (Playwright) and run against a live bench.
- `pre-commit` hooks run `ruff format` + `ruff check` on staged Python
  files plus trailing-whitespace / EOF / YAML / JSON sanity checks.
  Install locally with:
  ```bash
  pip install pre-commit
  pre-commit install
  ```

### Frontend tests

Playwright smoke tests live in `frontend-tests/`. See
[`frontend-tests/README.md`](frontend-tests/README.md) for setup, the
four specs (send-prompt, mode-switcher, preview-approve, rollback),
the stable `data-testid` catalogue, and the `ALFRED_RUN_SLOW_TESTS=1`
gate on the two destructive specs.

### Alfred chat Vue composables

The chat UI lives in `alfred_client/public/js/alfred_chat/`.
`AlfredChatApp.vue` is the root SFC; cross-cutting logic is factored
into composables under `composables/`:

| File | Responsibility |
|---|---|
| `useDrawerState.js` | Right-hand preview drawer open/close, localStorage persistence |
| `useConversationAdmin.js` | Load / delete / share / health-dialog for conversations |
| `usePreviewActions.js` | Approve / Request Changes / Reject / Rollback buttons |
| `useAlfredRealtime.js` | The 15 `frappe.realtime.on` handlers that bridge WS events to chat state |

When adding a new realtime event or preview-panel action, prefer
extending the relevant composable over adding more code to
`AlfredChatApp.vue` - the SFC is already large and the composables
keep the blast radius of each change small.

## License

MIT
