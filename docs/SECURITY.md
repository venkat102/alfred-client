# Alfred Security Model

This document covers Alfred's trust boundaries, authentication, authorization,
data handling, and how to report a vulnerability. Developers should read the
[Permission Model section of how-it-works.md](how-it-works.md#permission-model)
alongside this for the full picture.

## Reporting a vulnerability

**Do not open a public issue.** Email the maintainer privately with:

1. A reproducer (ideally with a failing test case).
2. The affected component (`alfred_client`, `alfred_processing`, `alfred_admin`).
3. Your assessment of impact (data disclosure / privilege escalation /
   denial of service / integrity).
4. Whether the vulnerability is already public anywhere.

We aim to acknowledge within 72 hours, ship a fix (or mitigation) within 14
days for high/critical issues, and credit reporters in the changelog unless
they request otherwise.

## Trust boundaries

Alfred spans three processes, each with its own trust boundary:

```
Browser
   │ Frappe session cookie (standard Frappe auth)
   ▼
┌──────────────────────────────────────────────┐
│ Customer Frappe Site (alfred_client app)     │
│                                              │
│ TRUST: whoever is logged in as a Frappe      │
│        user with a role in Alfred Settings > │
│        Allowed Roles. Administrator bypasses │
│        the role check.                       │
└──────────┬───────────────────────────────────┘
           │
           │ WebSocket outbound:
           │   - API_SECRET_KEY (shared secret)
           │   - JWT signed with the same key,
           │     embedding {user, roles, site_id,
           │     iat, exp}. user == conversation
           │     owner, not the caller.
           ▼
┌──────────────────────────────────────────────┐
│ Processing App (alfred_processing)           │
│                                              │
│ TRUST: whoever holds API_SECRET_KEY. A       │
│        forged JWT requires the same key, so  │
│        knowing the secret = full impersonation│
│        of any user on any site.              │
└──────────┬───────────────────────────────────┘
           │
           │ HTTPS:
           │   - Authorization: Bearer <service_api_key>
           ▼
┌──────────────────────────────────────────────┐
│ Admin Portal (alfred_admin)                  │
│                                              │
│ TRUST: whoever holds ADMIN_SERVICE_KEY for   │
│        check_plan / report_usage /           │
│        register_site. Billing endpoints      │
│        (subscribe / cancel) require a Frappe │
│        login with System Manager role.       │
└──────────────────────────────────────────────┘
```

Each boundary is a single credential. **Losing one credential does not
compromise the others** - e.g., an attacker with the admin portal's
`ADMIN_SERVICE_KEY` still cannot open WebSocket connections to the
processing app (different key). But within a boundary, the credential is
high-trust and should be treated like a root password.

### Key rotation

| Credential | Stored | Rotate when |
|---|---|---|
| `API_SECRET_KEY` | Processing app `.env` + `Alfred Settings.api_key` (encrypted password field on the Frappe side) | Employee offboarding, suspected exposure, after any security incident. Requires restarting the processing app + updating every customer site's Alfred Settings. |
| `ADMIN_SERVICE_KEY` | Admin portal `Alfred Admin Settings.service_api_key` + processing app `.env` `ADMIN_SERVICE_KEY` | Same triggers as above. |
| LLM API keys (Anthropic, OpenAI, Google) | `Alfred Settings.llm_api_key` | Provider breach notification, billing anomaly. |

Store `.env` outside version control (`.gitignore` should cover it). Use a
secret manager in production (AWS Secrets Manager, GCP Secret Manager,
HashiCorp Vault, Doppler) rather than pasting keys into environment files
by hand.

**Rotating `API_SECRET_KEY`:** the processing app ships a helper script.
From the `alfred_processing` repo:

```bash
python scripts/rotate_api_secret_key.py
```

It generates a strong key, backs up the current `.env` (timestamped),
writes the new key, and prints follow-up steps: paste the new key into
Alfred Settings on every site that talks to this processing app, then
restart the processing app. Startup validation (see below) will refuse
to boot on a short or placeholder key.

### Startup validation (processing app)

`alfred.config.Settings` refuses to boot when `API_SECRET_KEY` is
shorter than 32 characters or matches a known-weak placeholder
(`changeme`, `secret`, `dev`, `test`, `your-secret-key`, ...). This
closes the window where an operator copies `.env.example`, forgets to
rotate, and ships with a guessable key. The error message points at
`scripts/rotate_api_secret_key.py` so the fix is one command away.

## Authentication

### Browser → Frappe site
Standard Frappe session cookies. Alfred adds no new auth path here; if you
can log in to Frappe, you can access the Alfred pages you have permission
for (gated by `validate_alfred_access()` and the Allowed Roles list in
Alfred Settings).

### Frappe site → Processing app (WebSocket)
Two-factor handshake:

1. **Shared API key** (`API_SECRET_KEY`). Must match on both sides. Attached
   as `api_key` in the first WebSocket message.
2. **Signed JWT** (HS256, signed with `API_SECRET_KEY`). Embeds
   `{user, roles, site_id, iat, exp}`. The `user` is the **conversation
   owner**, not the caller - see "Authorization" below for why.

The JWT has a 24-hour expiry. A forged JWT would need `API_SECRET_KEY`, at
which point the attacker could impersonate any user on any site anyway.

**`exp` claim is required.** `verify_jwt_token` pins
`options={"require": ["exp"]}` on decode, so a hand-crafted token with
a valid signature but no expiry is rejected up-front. Empty or `None`
token strings also raise immediately. These guards prevent the footgun
where a forged token would never expire. (`alfred/middleware/auth.py`,
regression tests in `tests/test_api_gateway.py::TestJWT`.)

**API key comparison is constant-time.** Both the REST Bearer-token
check and the WebSocket handshake compare the submitted key against
`API_SECRET_KEY` using `hmac.compare_digest(...)`, not `==`. A naive
equality check leaks the prefix match length via response latency,
which is enough to reconstruct the key one byte at a time across
enough requests from a co-located attacker. `compare_digest` evaluates
in time proportional to the longer input regardless of where the
mismatch lies. Regression test:
`tests/test_api_gateway.py::TestWebSocket::test_ws_rejects_same_length_wrong_key`.

**Processing App URL is scheme-validated on save.** Alfred Settings
rejects `processing_app_url` values that use plaintext `http://` /
`ws://` against non-loopback hosts, because the WebSocket handshake
embeds `llm_api_key` inside `site_config` - over plaintext, that key
rides the wire in cleartext on every connection. Accepted schemes:
`https://` and `wss://` universally; `http://` and `ws://` only when
the host is `localhost`, `127.x.x.x`, or `::1` (the local-dev
convenience case). Validation logic:
`alfred_client.alfred_settings.doctype.alfred_settings.alfred_settings._check_processing_app_url`.
Regression test:
`alfred_client/test_alfred_settings.py::run_tests`.

### Processing app → Admin portal
`Authorization: Bearer <service_api_key>` header. Admin portal endpoints
use `_validate_service_key()` to verify. An empty key is always rejected.

### Processing app ← Admin portal (reverse direction)
Does not happen. The admin portal only responds to requests from the
processing app; it never initiates a call back.

## Authorization

### Six-layer permission model

The processing app and client app share a **defense-in-depth** model.
A request has to pass every layer that applies to it.

| Layer | Where | What it enforces |
|---|---|---|
| 1. UI access | `validate_alfred_access()` on page loads and every whitelisted endpoint | Role membership in `Alfred Settings > Allowed Roles`. Administrator bypasses. |
| 2. Endpoint ownership | Every sensitive endpoint calls `frappe.has_permission("Alfred Conversation" \| "Alfred Changeset", ptype="read" \| "write", throw=True)` | Only the conversation owner, shared-with users (via `frappe.share.add`), or System Managers can read/write a conversation or its changesets. |
| 3. API transport | Shared API key + JWT | Only the processing app (or anyone holding `API_SECRET_KEY`) can connect. |
| 4. MCP session | `_connection_manager` calls `frappe.set_user(conversation_owner)` at start, restores in `finally` | Every MCP tool call dispatched by the agent runs under the **conversation owner's** session. Not the caller, not Administrator. Row-level `permission_query_conditions` filters apply. |
| 5. Tool-level checks | Each MCP tool in the registry uses `frappe.has_permission` / `frappe.get_meta` / `frappe.get_doc` which respect `frappe.session.user` | Per-tool per-row permission enforcement. `check_permission` is also exposed as a tool so agents can gate their own plans. |
| 6. Deploy-time re-check | `apply_changeset` loops over each item and calls `frappe.has_permission(doctype, action, throw=True)` before each create/update | Even if layers 1-5 let a request through, the write itself still enforces live permissions. Operations use `ignore_permissions=False` (full check). |

### Why the MCP session runs as the owner

If user A opens user B's shared conversation and the MCP tool calls ran as
user A, the agent would see **user A's** view of the data - whatever user A
has read access to. That's wrong: the conversation belongs to user B, the
agent is working on B's problem, so the agent should see B's world. Running
as the owner also prevents information leakage where A sees data B can't
see and pastes it into B's chat.

`start_conversation` enforces this: it loads `conv.user` from the DocType
and passes that to the connection manager as the session identity, not
`frappe.session.user`. Before this fix (see CHANGELOG), shared conversations
silently ran with the caller's permissions.

### Billing endpoint gating (admin portal)

`subscribe_to_plan` and `cancel_subscription` use `ignore_permissions=True`
internally for customer + subscription mutations. Without an explicit role
gate, any logged-in portal user could call them via the Frappe whitelist
endpoint. Both now call `_require_billing_admin()` which throws if the
caller lacks the System Manager role.

### Three-mode chat attack surface (Phase A-D)

The three-mode chat feature introduces `chat` / `insights` / `plan` modes
that run alongside `dev` (the existing crew pipeline). None of the three
new modes can write to the DB:

- **Chat mode** runs a single LLM call with no MCP tool bindings. It
  cannot read or modify site state.
- **Insights mode** runs a single-agent crew bound to a **read-only
  MCP tool subset** (`alfred/tools/mcp_tools.py::build_mcp_tools(...)["insights"]`).
  The write-shaped `dry_run_changeset` is explicitly excluded. The
  tool budget is hard-capped at 5 calls per turn.
- **Plan mode** runs a 3-agent crew (Requirement / Assessment /
  Architect) whose terminal task produces a JSON plan document, not a
  changeset. The crew has access to the same MCP tools as the planning
  stages of Dev mode but never reaches the Developer / Tester /
  Deployer agents. Tool budget is 15 calls per turn.

The Dev pipeline is still the only path that writes to the customer
site, and it still runs through every layer of the 6-layer model
including the pre-preview dry-run and approval gate. Plan → Dev
handoff (triggered by the "Approve & Build" button in the plan panel)
flows through the normal prompt → orchestrator → Dev path - the plan
doc is injected into the Dev crew's enhanced prompt as context, not
executed as-is.

The new client-side `alfred_chat.set_conversation_mode(conversation,
mode)` endpoint gates on `frappe.has_permission("Alfred Conversation",
ptype="write")` so only the conversation owner (or a user it was shared
with, or System Manager) can change the sticky mode preference.

## Data handling

### What Alfred sees and sends to the LLM

- **Conversation messages** - every message in the chat thread.
- **Site schema** - DocType definitions, field names, field types,
  permissions. Via `lookup_doctype` / `get_doctype_schema`.
- **Existing customizations** - Custom Fields, Server Scripts, Client
  Scripts, Workflows already on the site. Via `get_existing_customizations`.
- **User context** - username, roles, permitted modules. Via
  `get_user_context`.
- **Framework Knowledge Graph** - vanilla DocType metadata from installed
  bench apps, packaged with the client app.

### What Alfred **never** sees or sends

- **Row-level data**. Alfred tools read DocType *metadata*, not document
  records. The only exception is `check_has_records(doctype)` which
  returns a count, not rows, and only to decide whether rollback deletion
  is safe.
- **File attachments**.
- **Passwords**. Frappe's password fields are hashed/encrypted at the DB
  level; Alfred's tools don't bypass this.
- **API keys**. `Alfred Settings.api_key` and `llm_api_key` are encrypted
  password fields; even the MCP tools can't read them as plaintext unless
  they call `.get_password()` explicitly (none of the current tools do).

### Where data is processed

Depends on the LLM configuration:

| LLM Provider | Where conversation + schema data goes |
|---|---|
| Ollama (local) | Stays on your server. Nothing leaves your network. |
| Ollama (remote, internal) | Stays on your network, but on a different host. |
| Anthropic Claude | Sent to Anthropic's API over TLS. Subject to Anthropic's data retention + usage policies. |
| OpenAI GPT | Sent to OpenAI's API. Subject to OpenAI's policies. |
| Google Gemini | Sent to Google's API. |
| AWS Bedrock | Stays inside your AWS account if the Bedrock endpoint is in your VPC. |

Nothing is sent through a third party that isn't your chosen LLM provider.
Alfred does not ship telemetry, analytics, or error reporting to us by
default. (If you enable `ALFRED_TRACING_ENABLED`, the trace JSONL is
written locally - we don't collect it.)

**CrewAI outbound telemetry is disabled by default.** CrewAI (our agent
framework dependency) ships with a built-in exporter that would POST
agent run metadata to its own SaaS endpoint. The `Dockerfile` and
`.env.example` set three env flags to shut this off:
`CREWAI_DISABLE_TELEMETRY=true`, `CREWAI_DISABLE_TRACKING=true`,
`OTEL_SDK_DISABLED=true`. Keep these set unless you specifically want
CrewAI to see your agent run metadata. All three are checked to cover
older + newer CrewAI versions.

### What's stored on your site

- **Alfred Conversation** - chat threads.
- **Alfred Message** - individual messages.
- **Alfred Changeset** - proposed JSON changesets + dry-run results +
  deployment log + rollback data.
- **Alfred Audit Log** - write-ahead record of every deploy step with
  before/after state snapshots. Intended for compliance: every
  Alfred-made change is traceable.
- **Alfred Created Document** - child table on Alfred Conversation
  listing every document created by Alfred in that session.

All subject to Frappe's standard backup + restore. No data is stored
outside the Frappe site database (except the trace JSONL file on the
processing host if tracing is enabled - rotate / delete per your
retention policy).

## Known security-relevant config

Watch these fields when configuring for production:

| Field | Where | Default | Production setting |
|---|---|---|---|
| `API_SECRET_KEY` | Processing app `.env` | none | 32+ random bytes, generated with `secrets.token_urlsafe(32)` |
| `ALLOWED_ORIGINS` | Processing app `.env` | `*` | Comma-separated list of your Frappe site URLs. Never leave as `*` in production - same-origin isn't enforced by WebSockets, but CORS still helps for the HTTP routes. |
| `DEBUG` | Processing app `.env` | `false` | `false` in production. Debug mode surfaces stack traces in error responses. |
| `ADMIN_SERVICE_KEY` | Processing app `.env` + admin portal settings | none | 32+ random bytes, separate from `API_SECRET_KEY`. |
| `Alfred Settings.api_key` | Frappe site | none | Must match `API_SECRET_KEY` exactly. Encrypted in Frappe. |
| `Alfred Settings.allowed_roles` | Frappe site | empty (falls back to System Manager) | Explicit list. Don't leave empty unless you only want System Managers using Alfred. |
| `Alfred Settings.enable_auto_deploy` | Frappe site | `false` | **Leave `false`** unless you fully trust Alfred's output for your site's workflow. Auto-deploy skips the preview + approval step. |
| Frappe HTTPS | Frappe site | varies | **Required** in production. Alfred uses Frappe session auth; without HTTPS, session cookies travel in plaintext. |

### Outbound telemetry and trace files

**CrewAI SaaS telemetry is off by default.** CrewAI ships with a built-in
exporter that POSTs agent run metadata (task descriptions, agent
decisions, LLM usage counts) to CrewAI's own endpoint unless you
disable it. `alfred/main.py` calls `os.environ.setdefault()` on
`CREWAI_DISABLE_TELEMETRY`, `CREWAI_DISABLE_TRACKING`, and
`OTEL_SDK_DISABLED` at import time, so a local `.env` that omits those
three lines still starts with telemetry off. To opt BACK IN for
debugging, set `CREWAI_DISABLE_TELEMETRY=false` in your environment
(setdefault respects explicit values). Regression test:
`tests/test_main_bootstrap.py`.

**Tracer output path is whitelisted.** When `ALFRED_TRACING_ENABLED=1`,
the JSONL tracer writes to `ALFRED_TRACE_PATH`. An attacker who can
set process env vars (container env injection, CI secret leakage)
could otherwise redirect those writes to an arbitrary path like
`/etc/systemd/system/<service>.override`. `_safe_trace_path()` in
`alfred/obs/tracer.py` rejects inputs containing `..` and only accepts
resolved paths inside CWD, `$HOME`, `tempfile.gettempdir()`, `/tmp`,
or `/var/tmp`. Anything else logs a WARNING and falls back to the
default `alfred_trace.jsonl`. macOS `/tmp` → `/private/tmp` symlink is
handled by running `realpath()` on both sides. Regression tests:
`tests/test_tracer_path_validation.py`.

## Known risks and mitigations

### Prompt injection

User prompts are LLM input. A malicious user could try to manipulate the
agent into producing harmful changesets. Mitigations:

1. `alfred/defense/sanitizer.py` blocks obvious injection patterns
   (`check_prompt`) as the first pipeline phase.
2. The preview + approval step requires a human to review every
   changeset before it touches the DB (unless `enable_auto_deploy` is on,
   which we recommend leaving off).
3. Dry-run validation catches broken / invalid changesets that would fail
   at insert time. A malicious prompt can't produce a valid-but-harmful
   Server Script without also passing the user's preview review.
4. Deploy-time `frappe.has_permission` re-check means even if an
   adversarial prompt made it to deploy, operations still enforce the
   conversation owner's real permissions.

**Residual risk**: a crafted prompt could produce a changeset that looks
benign in the preview but does something subtle at runtime (e.g., a
Server Script with a hidden side effect). Read Server Script / Client
Script code carefully before approving.

### Secrets in prompts

Users sometimes paste passwords / API keys into chat messages. Alfred
doesn't strip these; they become part of the conversation in the DB and
in whatever LLM provider you're using.

**Mitigation**: train users not to paste secrets. Consider enabling a
client-side prompt-scrubber (not built into Alfred today - tracked as
future work).

### LLM provider data retention

Cloud LLM providers may retain conversation data for training or abuse
monitoring. Read each provider's policy. If you're processing sensitive
data, use **local Ollama** (data never leaves your server) or **AWS
Bedrock in-VPC** (data stays in your AWS account).

### Worker slot exhaustion

Each active conversation consumes one slot on the `long` RQ worker queue
for up to 2 hours. A malicious user with Alfred access could open many
conversations to exhaust the worker pool. Mitigations:

1. `max_tasks_per_user_per_hour` rate limit in Alfred Settings (default
   20). Enforced on BOTH paths now: REST POST /api/v1/tasks (returns
   429 with Retry-After) and WebSocket prompt messages (returns an
   `error` event with `code=RATE_LIMIT`, `retry_after`, `limit`).
   Clarifier-answer prompts do not count against the limit since
   they're continuation of a running task.
2. `max_lifetime_seconds = 6300` cap in the connection manager - idle
   conversations get reaped after ~1h45m.
3. `stop_conversation` is now gated on write permission, so one user
   can't force-stop another's pipeline.

### SQL injection via agent-generated Server Scripts

A Server Script the agent generates is executed on your Frappe site with
the conversation owner's permissions. Frappe Server Scripts run in a
sandboxed Python environment (restricted builtins, no `os` / `sys` /
`subprocess` imports), but SQL can still be passed to `frappe.db.sql`.

**Mitigation**: `dry_run_changeset` runs `compile()` on every Server
Script to catch syntax errors, and the agent's task descriptions direct
it to use parameterized queries. **Manually review** any agent-generated
Server Script that concatenates user input into SQL before approving.

### Dry-run DDL leakage (fixed)

Prior to 2026-04-13, the dry-run path called `.insert()` on DocType /
Custom Field / Workflow changes inside a savepoint. MariaDB implicitly
commits pending DML before DDL, so the savepoint rollback silently left
the changes in the DB. See the CHANGELOG for the full fix. If you're
running an older build, upgrade immediately.

### Authorization bypass on changeset endpoints (fixed)

Prior to 2026-04-13, several endpoints (`approve_changeset`,
`reject_changeset`, `apply_changeset`, `rollback_changeset`,
`start_conversation`, etc.) only ran `validate_alfred_access()` - a
coarse role gate. Any user with the Alfred role could approve / deploy /
rollback / start another user's conversation work. All now call
`frappe.has_permission(...)` with an explicit `ptype="write"` or
`ptype="read"` check. See the CHANGELOG for the full list.

## Security checklist for production deploy

- [ ] `API_SECRET_KEY` generated with `secrets.token_urlsafe(32)`,
      different from any other secret.
- [ ] `ADMIN_SERVICE_KEY` generated separately, different from
      `API_SECRET_KEY`.
- [ ] `ALLOWED_ORIGINS` set to explicit site URLs (not `*`).
- [ ] `DEBUG=false` on the processing app.
- [ ] HTTPS terminated in front of the processing app (nginx / ALB /
      cloudflare).
- [ ] HTTPS on the Frappe site.
- [ ] `Alfred Settings.allowed_roles` explicitly populated.
- [ ] `Alfred Settings.enable_auto_deploy` left off.
- [ ] `worker_long` entry added to the `Procfile`.
- [ ] Backups running on the Frappe site (includes Alfred data).
- [ ] `.env` files NOT in version control; secrets stored in a secret
      manager.
- [ ] Cloud LLM provider data retention policy reviewed (if not using
      local Ollama).
- [ ] Every user has a unique Frappe account; no shared logins.
- [ ] System Manager role granted sparingly.
- [ ] At least one test rollback performed in staging to verify the
      `rollback_changeset` path works for your environment.
