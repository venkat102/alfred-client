# Alfred Developer & API Reference

## Processing App REST API

Base URL: `http://processing-host:8001`

### Health Check
```
GET /health
Response: {"status": "ok", "version": "0.1.0", "redis": "connected"}
```

### Create Task
```
POST /api/v1/tasks
Authorization: Bearer <api_key>

Body:
{
  "prompt": "Create a DocType called Book",
  "user_context": {"user": "admin@site.com", "roles": ["System Manager"]},
  "site_config": {"site_id": "site.frappe.cloud", "llm_model": "ollama/llama3.1", "max_tasks_per_user_per_hour": 20}
}

Response (201):
{"task_id": "uuid", "status": "queued"}
```

### Get Task Status
```
GET /api/v1/tasks/{task_id}?site_id=...
Authorization: Bearer <api_key>

Response: {"task_id": "...", "status": "running", "current_agent": "Architect"}
```

### Get Task Messages
```
GET /api/v1/tasks/{task_id}/messages?site_id=...&since_id=0
Authorization: Bearer <api_key>

Response: [{"id": "stream-id", "data": {...}}]
```

### Error Responses
```json
{"error": "message", "code": "ERROR_CODE"}

Codes: AUTH_MISSING, AUTH_INVALID, RATE_LIMIT, TASK_NOT_FOUND, REDIS_UNAVAILABLE
```

---

## WebSocket Protocol

### Connection
```
ws://processing-host:8001/ws/{conversation_id}
```

### Handshake (first message from client)
```json
{
  "api_key": "shared-secret",
  "jwt_token": "eyJ...",
  "site_config": {
    "llm_provider": "ollama",
    "llm_model": "ollama/llama3.1",
    "llm_max_tokens": 4096,
    "max_tasks_per_user_per_hour": 20
  }
}
```

### Auth Success Response
```json
{"msg_id": "...", "type": "auth_success", "data": {"user": "...", "site_id": "...", "conversation_id": "..."}}
```

### Message Types (Server -> Client)
| Type | Description |
|------|------------|
| `auth_success` | Handshake accepted |
| `agent_status` | Agent started/completed |
| `question` | Agent asking user a question |
| `preview` | Changeset preview data |
| `changeset` | Full changeset for approval |
| `echo` | Echo response (for testing) |
| `error` | Error message |
| `ping` | Heartbeat (every 30s) |
| `mcp_response` | MCP tool call result |

### Message Types (Client -> Server)
| Type | Description |
|------|------------|
| `prompt` | User's text message |
| `user_response` | Answer to agent question |
| `deploy_command` | Deployment trigger |
| `ack` | Message acknowledgment |
| `resume` | Reconnection with last_msg_id |

### Message Envelope
```json
{"msg_id": "uuid", "type": "prompt", "data": {"text": "Create a Book DocType"}}
```

---

## MCP Tools Reference

All tools callable via JSON-RPC 2.0 over WebSocket.

### tools/list
```json
Request:  {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
Response: {"jsonrpc": "2.0", "id": 1, "result": {"tools": [...]}}
```

### tools/call
```json
Request:  {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "get_site_info"}, "id": 2}
Response: {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "..."}]}}
```

### Available Tools

| Tool | Tier | Args | Returns |
|------|------|------|---------|
| `get_site_info` | 1 | none | frappe_version, installed_apps, company, country |
| `get_doctypes` | 1 | module? | [{name, module}] |
| `get_doctype_schema` | 2 | doctype | fields, permissions, naming_rule |
| `get_existing_customizations` | 2 | none | custom_fields, server_scripts, client_scripts, workflows |
| `get_user_context` | 2 | none | user, roles, permitted_modules |
| `check_permission` | 3 | doctype, action | {permitted: bool} |
| `validate_name_available` | 3 | doctype, name | {available: bool} |
| `has_active_workflow` | 3 | doctype | {has_active_workflow: bool} |
| `check_has_records` | 3 | doctype | {has_records: bool, count: int} |

---

## Admin Portal API

Base URL: `http://admin-site:8000`
Auth: `Authorization: Bearer <service_api_key>`

### Report Usage
```
POST /api/method/alfred_admin.api.usage.report_usage
Body: {"site_id": "...", "tokens": 1500, "conversations": 1, "active_users": 3}
Response: {"status": "ok"}
```

### Check Plan
```
POST /api/method/alfred_admin.api.usage.check_plan
Body: {"site_id": "..."}
Response: {"allowed": true, "remaining_tokens": 85000, "tier": "Pro", "warning": null}
```

### Register Site
```
POST /api/method/alfred_admin.api.usage.register_site
Body: {"site_id": "...", "site_url": "https://...", "admin_email": "admin@..."}
Response: {"status": "created", "plan": "Free"}
```

---

## Agent Output Schemas

All agent outputs are validated against Pydantic models in `alfred/models/agent_outputs.py`.

### RequirementSpec
```json
{"summary": "...", "customizations_needed": [...], "dependencies": [...], "open_questions": [...]}
```

### AssessmentResult
```json
{"verdict": "ai_can_handle|needs_human|partial", "permission_check": {"passed": bool, "failed": [...]}, "complexity": "low|medium|high", "risk_factors": [...]}
```

### ArchitectureBlueprint
```json
{"documents": [{"order": 1, "operation": "create", "doctype": "DocType", "name": "...", "design": {...}}], "deployment_order": [...], "rollback_safe": true}
```

### Changeset
```json
{"items": [{"operation": "create", "doctype": "DocType", "data": {...complete definition...}}]}
```

### TestReport
```json
{"status": "PASS|FAIL", "issues": [{"severity": "critical|warning", "item": "...", "issue": "...", "fix": "..."}], "summary": "..."}
```

### DeploymentResult
```json
{"plan": [...], "approval": "approved|rejected", "execution_log": [...], "rollback_data": [...], "documents_created": [...]}
```

---

## Adding New MCP Tools

1. Add the tool function in `alfred_client/mcp/tools.py`
2. Register it in `TOOL_REGISTRY` dict at the bottom of the file
3. Add a CrewAI wrapper in `alfred/tools/mcp_tools.py` on the processing app
4. Add to the appropriate agent's tool list in `alfred/agents/tool_stubs.py` → `TOOL_ASSIGNMENTS`
5. Update the agent backstory if the tool changes the agent's capabilities

## Adding New Agents

1. Add backstory in `alfred/agents/backstories.py`
2. Add agent creation in `alfred/agents/definitions.py` → `build_agents()`
3. Add task in `alfred/agents/crew.py` → `TASK_DESCRIPTIONS` and `build_alfred_crew()`
4. Add output Pydantic model in `alfred/models/agent_outputs.py`
5. Add phase to the UI pipeline in `alfred.js` → `render_pipeline()`
