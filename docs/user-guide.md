# Alfred User Guide

A complete guide to using Alfred — from your first conversation to deployment and rollback.

---

## What is Alfred?

Alfred is an AI assistant that builds Frappe customizations through conversation. Describe what you need — a DocType, workflow, report, or automation — and Alfred designs it, generates the code, validates it, and deploys it to your site after your approval.

### What Alfred Can Build
- **DocTypes** — New document types with fields, permissions, naming rules, and child tables
- **Custom Fields** — New fields on existing DocTypes (like adding phone_number to Customer)
- **Server Scripts** — Python automation that runs on save, submit, or via API
- **Client Scripts** — JavaScript that customizes forms (filters, calculated fields, visibility)
- **Workflows** — Multi-state approval processes with role-based transitions
- **Notifications** — Email/SMS alerts triggered by document events
- **Reports** — Custom reports with filters and columns
- **Print Formats** — PDF templates for documents

### What Alfred Cannot Do
- Modify core Frappe or ERPNext source code files
- Run bench commands or shell operations
- Access your server's file system
- Make changes requiring app-level Python files (hooks.py, custom app code)
- Build features that don't exist in Frappe's customization framework (like real-time dashboards or external API integrations)

---

## The Interface

When you open `/app/alfred`, you see two panels:

```
┌──────────────────────┬────────────────────────────────────┐
│  Status Bar: Agent name, phase pipeline (1-6), timer      │
├──────────────────────┬────────────────────────────────────┤
│                      │                                    │
│  Left Panel (40%)    │  Right Panel (60%)                 │
│                      │                                    │
│  Conversation List   │  Preview Panel                     │
│  or                  │                                    │
│  Chat Messages       │  Shows what Alfred proposes:       │
│                      │  - DocType field tables             │
│                      │  - Script code                      │
│                      │  - Permission grid                  │
│                      │  - Deploy progress                  │
│                      │                                    │
│  ┌────────────────┐  │  [Approve] [Modify] [Reject]       │
│  │ Input box      │  │                                    │
│  └────────────────┘  │                                    │
└──────────────────────┴────────────────────────────────────┘
```

### Status Bar (top)
- **Status dot** — Green (ready), yellow pulsing (processing), blue pulsing (waiting for you), red (error)
- **Agent name** — Shows which agent is currently working (e.g., "Step 3/6 — Solution Architect is working...")
- **Timer** — Elapsed seconds since the current phase started
- **Pipeline** — Six numbered steps: Requirements → Assessment → Architecture → Development → Testing → Deployment. Current step is highlighted blue, completed steps are green with a checkmark.

### Left Panel
- **Conversation list** — Shows your past conversations with the first message as a summary, status badge, and relative time. Click to open.
- **Chat area** — When a conversation is open, shows the message thread and input box.

### Right Panel
- **Preview** — Empty until Alfred has something to show. During early phases, shows what Alfred is doing ("Gathering Requirements...", "Designing Solution..."). Once a changeset is ready, shows DocType field tables, script code, and permission grids.
- **Action buttons** — Appear when a changeset is ready for your review.

---

## Your First Conversation

### Step 1: Start

Open `/app/alfred`. If this is your first time, you'll see a welcome screen with three example prompts. Click one, or click **"Start a Conversation"** and type your own request.

**Good prompts are specific:**
> "Create a DocType called Training Program with fields: program_name (Data, required), duration_days (Int), trainer (Link to Employee), and status (Select: Draft/Active/Completed)"

**Vague prompts trigger questions:**
> "I need something for HR training"
>
> Alfred will ask: "What specific aspect of HR training do you want to manage? Training programs, training requests, attendance tracking, or something else?"

### Step 2: Alfred Gathers Requirements

The **Requirement Analyst** agent processes your request. You'll see:
- Status bar shows "Step 1/6 — Requirement Analyst is working..." with a pulsing yellow dot
- Bouncing dots (typing indicator) appear in the chat
- Preview panel shows "Gathering Requirements..."

**If your request is clear**, the agent moves to the next phase automatically.

**If your request is ambiguous**, a question card appears:

```
┌─────────────────────────────────────────────────────┐
│ (?) What fields should the Training Program have?    │
│                                                     │
│     [Name & Duration]  [Full Details]  [Custom]     │
│                                                     │
│     Alfred is waiting for your response              │
└─────────────────────────────────────────────────────┘
```

Click an option button or type your own answer. The input box re-enables automatically when Alfred asks a question.

### Step 3: Feasibility Check

The **Feasibility Assessor** verifies:
- You have permission to create the requested customization types
- No naming conflicts with existing DocTypes
- No workflow conflicts

**If everything passes**, it moves to design.

**If permissions are missing**, you'll see an error:
> "You don't have permission for this operation. Contact your administrator."
>
> This means your Frappe role doesn't include the permissions needed (usually System Manager). Ask your site admin to add your role to Alfred Settings → Allowed Roles.

### Step 4: Design & Development

The **Solution Architect** designs the technical solution, then the **Frappe Developer** generates the actual code. The preview panel updates progressively:

- "Designing Solution..." → "Generating Code..."
- Once complete, the preview shows the full changeset

### Step 5: Validation

The **QA Validator** checks everything:
- Python syntax in Server Scripts
- JavaScript syntax in Client Scripts
- Field types are valid Frappe types
- Naming conflicts don't exist
- Permission checks are present in scripts
- Deployment order is correct (dependencies first)

**If validation fails**, Alfred automatically asks the Developer to fix the issues and re-validates. This can happen up to 3 times. If it still fails after 3 attempts, the conversation is escalated to a human developer.

### Step 6: Review & Approve

The preview panel shows the complete changeset:

**DocType preview:**
| Field | Type | Label | Required |
|-------|------|-------|----------|
| program_name | Data | Program Name | Yes |
| duration_days | Int | Duration (Days) | |
| trainer | Link | Trainer | |
| status | Select | Status | |

**Permission preview:**
| Role | Read | Write | Create | Delete |
|------|------|-------|--------|--------|
| System Manager | Yes | Yes | Yes | Yes |

**Script preview** (dark code block with syntax highlighting)

Below the preview, a summary shows: "3 operation(s) will be applied to your site"

Three buttons appear:

- **Approve & Deploy** — A confirmation dialog shows exactly what will be created. Click "Yes" to deploy.
- **Request Changes** — The input box focuses with placeholder "What would you like to change?" Type what you want different (e.g., "Add a description field and make status required").
- **Reject** — Cancels the changeset. You can start over with a new prompt.

### Step 7: Deployment

After you approve:
- The preview panel shows a step-by-step progress tracker:
  ```
  ✓ Training Program (DocType)        Created
  ✓ validate_training (Server Script)  Created
  ⏳ Training Workflow                  In progress...
  ```
- Each step updates in real time
- On success: green banner "All changes deployed successfully"
- On failure: error message with automatic rollback, retry button available

### Step 8: After Deployment

Your customization is live. You can:
- Navigate to the new DocType (e.g., `/app/training-program`)
- Continue the conversation with follow-up requests ("Now add a child table for training sessions")
- Start a new conversation for a different customization

---

## Managing Conversations

### Conversation List

Your past conversations are listed with:
- **Summary** — First message you sent (truncated to 80 characters)
- **Status badge** — Color-coded current status
- **Time** — When last active ("2 minutes ago", "Yesterday at 3:15 PM")

### Conversation Statuses

| Status | Color | Meaning | What to Do |
|--------|-------|---------|------------|
| Open | Blue | Created but no messages yet | Send your first prompt |
| In Progress | Orange | Agents are actively working | Wait — or answer if asked |
| Awaiting Input | Yellow | Alfred asked a question | Answer the question |
| Completed | Green | All done, changes deployed | Nothing — or ask a follow-up |
| Escalated | Red | Too complex for AI | A human developer will handle it |
| Failed | Red | Something went wrong | Read the error, retry or start over |
| Stale | Gray | Inactive for 24+ hours | Open and continue, or start a new one |

### Finding a Conversation

Conversations show the first message as a summary. If you have many, scroll through the list — they're sorted by most recent activity.

---

## When Things Go Wrong

### Error Messages

Alfred translates technical errors into plain language:

| You See | What It Means |
|---------|--------------|
| "There was a problem with the data format" | The generated code had a structural issue — Alfred will retry |
| "You don't have permission for this operation" | Your Frappe role can't create this type of customization |
| "A document with this name already exists" | The DocType or script name conflicts with something on your site |
| "The operation took too long" | LLM or processing timeout — try again |
| "Could not connect to the processing service" | The processing app is down — contact your admin |
| "Your message was flagged by the security filter" | Your prompt matched a security pattern — rephrase it |

Every error message has:
- A **human-readable explanation** at the top
- An expandable **"Technical details"** section for admins
- A **"Retry"** button that resends your last message

### Escalation

If Alfred can't handle your request (too complex, repeated failures, ambiguous after 3 clarification attempts), the conversation is **escalated**:

1. Status changes to "Escalated"
2. System Managers receive an in-app notification and email
3. A human developer reviews the conversation and either:
   - **Takes over** — completes the work manually
   - **Returns to Alfred** — with clarified requirements for the AI to retry

You'll see a message in the chat: "This request has been escalated to a human developer."

### Rollback

If you need to undo a deployment:

1. Go to the Alfred Changeset record (linked from the conversation)
2. Click **"Rollback"**
3. Alfred checks if any created DocTypes have user-entered data:
   - **No data** → DocType is deleted cleanly
   - **Has data** → Deletion is skipped to protect your data. A message tells you the record count and suggests manual cleanup.
4. Updated documents are restored to their original state
5. Changeset status changes to "Rolled Back"

---

## Tips for Better Results

### Be Specific About Fields
Instead of: "Create a task tracker"
Say: "Create a DocType called Task with fields: title (Data, required), description (Text), priority (Select: Low/Medium/High/Critical), assigned_to (Link to User), due_date (Date), and status (Select: Open/In Progress/Done)"

### Mention Relationships
If your DocType needs to link to existing DocTypes, say so:
"Create a Training Request DocType with employee (Link to Employee), training_program (Link to Training Program), and request_date (Date)"

### Specify Permissions
"Only HR Managers should be able to create and approve training requests. Employees should be able to read their own."

### Specify Workflows Explicitly
"Create a workflow for Training Request: Draft → Submitted (by Employee) → Approved (by HR Manager) → Completed"

### One Thing at a Time
Alfred works best with focused requests. Instead of "build a complete HR module", break it down:
1. "Create a Training Program DocType with..."
2. "Create a Training Request DocType that links to Training Program..."
3. "Add a workflow to Training Request with approval..."
4. "Create a notification when Training Request is approved..."

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Enter | Send message |
| Shift + Enter | New line in message |
| Click option button | Auto-fills and sends that option |

---

## Data & Privacy

- **What Alfred sees**: Your conversation messages and your site's DocType structure (field names, types, permissions). Never your actual document data.
- **Where it's processed**: Depends on your LLM configuration:
  - **Ollama** (self-hosted) — Everything stays on your server. Nothing leaves your network.
  - **Cloud providers** (Claude, GPT, Gemini) — Conversation messages are sent to the provider's API. Your site schema is included for context. No document data is sent.
- **What's stored**: Conversations, messages, changesets, and audit logs are stored in your Frappe site's database. They can be viewed at `/app/alfred-conversation`.
- **Audit trail**: Every action Alfred takes is logged in the Alfred Audit Log with before/after state snapshots.
