# Frappe Bench Customizations for Alfred

Non-default configuration changes made to the Frappe bench at `~/bench/develop/frappe-bench/`.

## Procfile

**File:** `Procfile`

| Entry | Why |
|-------|-----|
| `worker_long` - `bench worker --queue long` | Alfred's WebSocket connection manager (`_connection_manager`) is enqueued to the `long` queue with a 2-hour timeout. The default Procfile only has a `default` queue worker, so long jobs never get picked up. Added 2026-04-11. |
