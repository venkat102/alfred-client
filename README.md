# Alfred

AI-powered assistant that builds Frappe/ERPNext customizations through conversation. Describe what you need - a DocType, workflow, script, or report - and Alfred designs, generates, validates, and deploys it to your site.

## Key Features

- **Three-mode chat** (feature-flagged via `ALFRED_ORCHESTRATOR_ENABLED=1` on the processing app) - Not every prompt is a build request. An orchestrator classifies each prompt into **Dev** (build and deploy via the 6-agent crew), **Plan** (produce a reviewable plan document via a 3-agent planning crew, no DB writes), **Insights** (read-only Q&A about your current site state, 5-call MCP tool budget, no DB writes), or **Chat** (conversational reply, no crew). Users can force a specific mode via the header switcher; Auto lets the orchestrator decide. Plan mode supports Plan → Dev handoff: clicking *Approve & Build* on a plan doc promotes it into a Dev run with the plan injected as an explicit spec. See [how-alfred-works.md](docs/how-alfred-works.md#chat-modes-and-the-orchestrator).
- **Full & Lite pipeline modes** - Within Dev mode, Full runs a 6-agent SDLC pipeline (highest quality, ~5-10 min). Lite runs a single-agent fast pass (~1 min, ~5x cheaper) for simple customizations. Set per-site via Alfred Settings or tier-locked via the admin portal's `Alfred Plan.pipeline_mode` field.
- **Live MCP tool calls (12 tools)** - Agents query the actual Frappe site (DocType schemas, permissions, existing customizations) during reasoning, not hardcoded snapshots. Every tool call streams to the UI as concrete progress ("Reading Leave Application schema...", "Checking write permission on Notification..."). Includes consolidated `lookup_doctype` (framework KG + live site merged view) and `lookup_pattern` (5 starter customization idioms with templates).
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
| **[API Reference](docs/developer-api.md)** | Developers | REST API, WebSocket protocol, 12 MCP tools, reflection, tracing, state machine internals |
| **[Data Model](docs/data-model.md)** | Developers | Field-by-field reference for every Alfred DocType + Redis key conventions |
| **[Debugging Guide](docs/debugging.md)** | Developers | Message flow, step-by-step verification, pipeline tracing, common problems |
| **[Benchmarking Guide](docs/benchmarking.md)** | Developers | Running the benchmark harness, reading the JSON output, gate thresholds, extending the prompt set |
| **[Self-Hosted Guide](docs/self-hosted-guide.md)** | Self-hosters | Quick-start for running your own processing app + Ollama |
| **[Changelog](../../../../alfred_processing/CHANGELOG.md)** | Everyone | Phase-by-phase history of what changed, when, and why |

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

## License

MIT
