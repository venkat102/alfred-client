# Alfred

AI-powered assistant that builds Frappe/ERPNext customizations through conversation. Describe what you need — a DocType, workflow, script, or report — and Alfred designs, generates, validates, and deploys it to your site.

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
