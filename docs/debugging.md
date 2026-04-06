# Alfred - Debugging Guide

A step-by-step guide to understanding what happens when you send a prompt, how to verify each step is working, and how to diagnose when something breaks.

---

## The Complete Message Flow

When you type a prompt in Alfred Chat and press Send, here's exactly what happens:

```
Step 1:  Browser → frappe.call("send_message")
              ↓
Step 2:  Frappe API saves Alfred Message in database (role=user)
              ↓
Step 3:  Frappe API enqueues connection manager as a background job
              ↓
Step 4:  Frappe API pushes the message to a Redis queue (list)
              ↓
Step 5:  Connection manager (background job) starts
              ↓
Step 6:  Connection manager opens WebSocket to Processing App
              ↓
Step 7:  Connection manager sends handshake (API key + JWT + site config)
              ↓
Step 8:  Processing App validates JWT, extracts user/site_id
              ↓
Step 9:  Connection manager drains Redis queue → sends prompt via WebSocket
              ↓
Step 10: Processing App runs prompt defense (sanitizer + intent classifier)
              ↓
Step 11: Processing App builds CrewAI crew and runs agent pipeline
              ↓
Step 12: Agents call LLM (Ollama/Claude/OpenAI) for reasoning
              ↓
Step 13: Processing App streams results back via WebSocket
              ↓
Step 14: Connection manager receives results
              ↓
Step 15: Connection manager calls frappe.publish_realtime() to browser
              ↓
Step 16: Browser receives Socket.IO event → updates chat UI
```

---

## Setting Up for Debugging

Open **3 terminals** side by side before sending a prompt:

### Terminal 1 - Processing App Logs (AI agents, WebSocket)

```bash
docker logs -f alfred_processing-processing-1 2>&1 | grep -v health
```

This shows: WebSocket connections, authentication, prompt receipt, agent pipeline execution, LLM calls, errors.

### Terminal 2 - Frappe Worker Logs (background jobs)

```bash
tail -f ~/bench/develop/frappe-bench/logs/worker.log ~/bench/develop/frappe-bench/logs/worker.error.log
```

This shows: connection manager job start/stop, Redis publish errors, background job failures.

### Terminal 3 - Browser Developer Console

Open `http://dev.alfred:8000/app/alfred-chat`, press **F12** (or Cmd+Option+I on Mac), go to the **Console** tab.

This shows: API call errors, Socket.IO events received, JavaScript errors.

---

## Step-by-Step Verification

After sending a prompt, check each step in order. The first step that fails is your root cause.

### Step 1-2: Message Saved?

**Check**: Open `/app/alfred-message` in your browser. Filter by your conversation.

**Expected**: Your message appears with `role = user`, `message_type = text`.

**If missing**:
- Check browser console (F12) for API errors
- The `send_message` API call failed
- Common cause: `validate_alfred_access()` rejected you - check your role is in Alfred Settings → Allowed Roles

### Step 3-4: Background Job Enqueued?

**Check**: Terminal 2 (worker.log)

**Expected**:
```
frappe.utils.background_jobs.execute_job(...
    method='alfred_client.api.websocket_client._connection_manager'...
```

**If missing**:
- Redis Queue is not running
- Fix: Make sure `bench start` is running (it starts Redis, workers, and web server)
- Verify: `redis-cli -p 11000 ping` should return `PONG`

### Step 5-6: WebSocket Connected?

**Check**: Terminal 1 (docker logs)

**Expected**:
```
INFO:  192.168.x.x:xxxxx - "WebSocket /ws/<conversation-id>" [accepted]
INFO:  connection open
```

**If missing**:
- Processing app is not reachable from the Frappe server
- Check: `curl http://localhost:8001/health` from the Frappe machine
- Verify the URL in Alfred Settings → Processing App URL matches the actual port
- If different machines: check firewall allows the port

### Step 7-8: Authentication Succeeded?

**Check**: Terminal 1 (docker logs)

**Expected**:
```
alfred.websocket INFO: WebSocket authenticated: user=you@example.com, site=http://dev.alfred, conversation=<id>
```

**If you see `connection open` but NOT `WebSocket authenticated`**:
- The API key in Alfred Settings does not match `API_SECRET_KEY` in `.env`
- Check both values are exactly the same (no trailing spaces, no quotes)
- The JWT warning `InsecureKeyLengthWarning` is not the cause - it's just a warning about short keys

**To verify keys match**:
```bash
# Check processing app key
docker exec alfred_processing-processing-1 env | grep API_SECRET

# Check Frappe key (run from bench directory)
bench --site dev.alfred execute "print(frappe.get_single('Alfred Settings').get_password('api_key'))"
```

### Step 9: Prompt Delivered?

**Check**: Terminal 1 (docker logs)

**Expected**:
```
alfred.websocket INFO: Custom message from you@example.com@http://dev.alfred: type=prompt
```

**If authenticated but no prompt**:
- The message was published to Redis before the connection manager subscribed
- This is a race condition - the queue drain should handle it
- Check: was the message pushed to the Redis list? The connection manager should log `Draining queued message for <conversation-id>`
- If not draining: the Redis instances may be different (Frappe uses port 11000, Docker uses port 6379)

### Step 10: Prompt Defense Passed?

**Check**: Terminal 1 (docker logs)

**Expected**: No `PROMPT_BLOCKED` or `NEEDS_REVIEW` error sent back.

**If blocked**:
- Your prompt triggered the security filter
- You'll see an error message in the chat: "Your message was flagged by the security filter"
- Rephrase the prompt to avoid patterns like "ignore instructions", "skip permissions", "execute SQL", etc.

### Step 11-12: Agent Pipeline Running?

**Check**: Terminal 1 (docker logs)

**Expected**:
```
alfred.websocket INFO: Running agent pipeline for conversation=<id>
alfred.crew INFO: Built 7 agents with LLM: ollama/llama3.3:70b
```

**If pipeline doesn't start**:
- Plan check may have failed (if admin portal is configured)
- Check for Python import errors in the logs

**If pipeline starts but hangs**:
- LLM (Ollama) may be unreachable or slow to respond
- Check: `curl http://your-ollama-ip:11434/api/tags` - should return model list
- Check: `curl http://your-ollama-ip:11434/api/generate -H "Content-Type: application/json" -d '{"model":"llama3.3:70b","prompt":"Hi","stream":false}'` - should return a response within 30 seconds
- If Ollama is loading the model for the first time, it may take 1-2 minutes

### Step 13-16: Results Returned to Browser?

**Check**: Terminal 3 (browser console) - look for Socket.IO events

**Expected**: Messages appear in the chat in real time.

**If processing app sends results but browser doesn't show them**:
- Socket.IO may not be connected
- Check browser console for `frappe.realtime` errors
- Verify `bench start` is running (Socket.IO needs the node process)

---

## Quick Diagnostic Commands

### One-liner: Check everything after sending a prompt

```bash
echo "=== Processing App (last 15 lines, no health checks) ===" && \
docker logs alfred_processing-processing-1 2>&1 | grep -v health | tail -15 && \
echo "" && \
echo "=== Frappe Worker Errors ===" && \
tail -10 ~/bench/develop/frappe-bench/logs/worker.error.log 2>/dev/null | grep -v "DeprecationWarning\|fork()" && \
echo "" && \
echo "=== Recent Background Jobs ===" && \
tail -5 ~/bench/develop/frappe-bench/logs/worker.log 2>/dev/null | grep "alfred"
```

### Check if services are running

```bash
echo "Processing App:" && curl -s http://localhost:8001/health | python3 -m json.tool 2>/dev/null || echo "NOT RUNNING"
echo ""
echo "Redis (Frappe):" && redis-cli -p 11000 ping 2>/dev/null || echo "NOT RUNNING"
echo ""
echo "Redis (Docker):" && docker exec $(docker ps -qf "name=redis") redis-cli ping 2>/dev/null || echo "NOT RUNNING"
echo ""
echo "Ollama:" && curl -s http://10.55.69.210:11434/api/tags 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print('RUNNING -', len(d.get('models',[])), 'models')" 2>/dev/null || echo "NOT REACHABLE"
```

### Check API key match

```bash
echo "Processing App key:" && docker exec alfred_processing-processing-1 env | grep API_SECRET_KEY
echo ""
echo "Alfred Settings key:" && cd ~/bench/develop/frappe-bench && bench --site dev.alfred execute "print(frappe.get_single('Alfred Settings').get_password('api_key'))" 2>/dev/null
```

### Check active WebSocket connections

```bash
docker logs alfred_processing-processing-1 2>&1 | grep "WebSocket" | tail -10
```

### Check conversation status in database

```bash
cd ~/bench/develop/frappe-bench && bench --site dev.alfred execute "print(frappe.get_all('Alfred Conversation', fields=['name','status','current_agent','modified'], order_by='modified desc', limit=5))"
```

---

## Common Problems and Fixes

### "Processing..." forever, nothing happens

| Check | Command | Expected |
|-------|---------|----------|
| Redis running? | `redis-cli -p 11000 ping` | `PONG` |
| Workers running? | `ps aux \| grep worker` | Multiple `frappe.worker` processes |
| Connection manager started? | `grep connection_manager ~/bench/develop/frappe-bench/logs/worker.log \| tail -3` | Job entry with your conversation ID |
| Processing app running? | `curl http://localhost:8001/health` | `{"status": "ok"}` |
| WebSocket connected? | `docker logs alfred_processing-processing-1 2>&1 \| grep "WebSocket" \| tail -3` | `[accepted]` and `connection open` |
| Auth passed? | `docker logs alfred_processing-processing-1 2>&1 \| grep "authenticated"` | `WebSocket authenticated: user=...` |

**Most common cause**: Redis Queue is down. Run `bench start` to start everything.

### Message saved but nothing else happens

The background job didn't run. This means:
1. Redis Queue is not running (port 11000)
2. Frappe workers are not running
3. Fix: `bench start` (starts web, workers, redis, socketio all together)

### WebSocket connects but auth fails silently

The API keys don't match. Both sides see the connection open, but the processing app closes it after JWT verification fails. Check:
```bash
# These two values MUST be identical:
docker exec alfred_processing-processing-1 env | grep API_SECRET_KEY
bench --site dev.alfred execute "print(frappe.get_single('Alfred Settings').get_password('api_key'))"
```

### Auth succeeds but prompt never arrives

Race condition: the message was published to Redis before the connection manager subscribed. The queue drain should handle this, but if the Redis instances are different (Frappe Redis on port 11000 vs Docker Redis on port 6379), the message goes to one Redis but the connection manager listens on the other.

**Fix**: The connection manager uses Frappe's Redis (from `frappe.conf.redis_queue`). Make sure the `.env` `REDIS_URL` for Docker and Frappe's `common_site_config.json` `redis_queue` point to accessible Redis instances.

### Pipeline starts but hangs at LLM call

Ollama is either:
1. Not running: `curl http://your-ollama-ip:11434/api/tags` fails
2. Model not pulled: the tags response doesn't list your model
3. Loading model for first time: first call takes 1-2 minutes to load into memory
4. Out of memory: check Ollama logs

```bash
# Test Ollama directly
curl http://10.55.69.210:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.3:70b","prompt":"Hello","stream":false}'
```

If this returns a response, Ollama is fine and the issue is in the pipeline code.

### Error in browser console: "Socket.IO disconnected"

The node socketio process is not running. This is part of `bench start`. If you started services individually, make sure to also run:
```bash
node socketio.js  # from the bench directory
```
Or just use `bench start` which starts everything.

---

## Log File Reference

| Log | Location | What It Shows |
|-----|----------|--------------|
| Processing app | `docker logs alfred_processing-processing-1` | WebSocket, auth, pipeline, LLM calls, errors |
| Frappe worker | `~/bench/develop/frappe-bench/logs/worker.log` | Background job starts and completions |
| Worker errors | `~/bench/develop/frappe-bench/logs/worker.error.log` | Python tracebacks from background jobs |
| Frappe web | `~/bench/develop/frappe-bench/logs/frappe.log` | API calls, page loads |
| Scheduler | `~/bench/develop/frappe-bench/logs/scheduler.log` | Scheduled jobs (stale cleanup, audit cleanup) |
| Bench | `~/bench/develop/frappe-bench/logs/bench.log` | Bench commands, migrations |
| Ollama | `docker logs <ollama-container>` or on Mac: `~/.ollama/logs/server.log` | Model loading, generation requests |
