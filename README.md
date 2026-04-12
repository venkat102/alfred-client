# Alfred

AI-powered assistant that builds Frappe/ERPNext customizations through conversation. Describe what you need - a DocType, workflow, script, or report - and Alfred designs, generates, validates, and deploys it to your site.

## Key Features

- **Full & Lite pipeline modes** - Full mode runs a 6-agent SDLC pipeline (highest quality, ~5-10 min). Lite mode runs a single-agent fast pass (~1 min, ~5× cheaper) for simple customizations. Set per-site via Alfred Settings or overridden per-subscription-tier by the admin portal.
- **Live MCP tool calls** - Agents query the actual Frappe site (DocType schemas, permissions, existing customizations) during reasoning, not hardcoded snapshots. Every tool call streams to the UI as concrete progress ("Reading Leave Application schema…", "Checking write permission on Notification…").
- **Pre-preview dry-run validation** - Every changeset is validated against your live database via savepoint rollback **before** you see the preview, so you only review deployable solutions. Includes Python syntax checks on Server Scripts and Jinja syntax checks on Notifications. Failed dry-runs trigger one automatic self-heal retry.
- **Approve-time safety net** - A second dry-run runs on Approve click to catch DB drift between preview and deploy.
- **Permission-aware** - All MCP tool calls and deployments run under the conversation owner's session, respecting `permission_query_conditions` and `frappe.has_permission` row-level filters.

## Documentation

| Document | Who It's For | What It Covers |
|----------|-------------|---------------|
| **[Setup Guide](docs/SETUP.md)** | Admins / DevOps | Installation, configuration, Docker setup, cloud LLM providers, production deployment, backup, monitoring |
| **[User Guide](docs/user-guide.md)** | End Users | How to use the chat, what each screen shows, conversation walkthrough, error handling, rollback, tips |
| **[Admin Guide](docs/admin-guide.md)** | Site Admins | Alfred Settings reference, admin portal config, processing app env vars |
| **[Architecture](docs/architecture.md)** | Developers | System design, agent pipeline, permission model, data flow diagrams |
| **[API Reference](docs/developer-api.md)** | Developers | REST API, WebSocket protocol, MCP tools, agent output schemas |
| **[Debugging Guide](docs/debugging.md)** | Developers | Message flow, step-by-step verification, log commands, common problems |

**Start here**: [Setup Guide](docs/SETUP.md) · **Something broken?** [Debugging Guide](docs/debugging.md)

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
