# Alfred User Guide

A complete guide to using Alfred - from your first conversation to deployment and rollback.

> **Haven't set up Alfred yet?** Read the [Setup Guide](SETUP.md) first. This guide assumes Alfred is already installed and configured on your Frappe site.

---

## What is Alfred?

Alfred is an AI assistant that builds Frappe customizations through conversation. Describe what you need - a DocType, workflow, report, or automation - and Alfred designs it, generates the code, validates it, and deploys it to your site after your approval.

### What Alfred Can Build
- **DocTypes** - New document types with fields, permissions, naming rules, and child tables
- **Custom Fields** - New fields on existing DocTypes (like adding phone_number to Customer)
- **Server Scripts** - Python automation that runs on save, submit, or via API
- **Client Scripts** - JavaScript that customizes forms (filters, calculated fields, visibility)
- **Workflows** - Multi-state approval processes with role-based transitions
- **Notifications** - Email/SMS alerts triggered by document events
- **Reports** - Custom reports with filters and columns
- **Print Formats** - PDF templates for documents

### What Alfred Cannot Do
- Modify core Frappe or ERPNext source code files
- Run bench commands or shell operations
- Access your server's file system
- Make changes requiring app-level Python files (hooks.py, custom app code)
- Build features that don't exist in Frappe's customization framework (like real-time dashboards or external API integrations)

---

## What the interface looks like

Alfred Chat is a conversation-first shell. Your transcript fills the
page; everything else (status, preview, navigation) floats on top so
the chat is never cropped or compressed.

- **Frosted topbar** (48px, sticky). Back button, a small gradient
  "A" mark + the conversation title, the mode switcher in the
  center, and the right zone for the preview toggle, "+ New", and
  the overflow menu (Health / Share / Delete). Frappe's empty
  breadcrumb strip above the page head is hidden so the topbar is
  the only chrome you see.
- **Floating status pill** centered near the top of the transcript.
  Idle state shows a small green dot with "Ready". While a run is
  processing, the pill switches to a pulsing chat-gradient mark +
  the current agent name + a live activity phrase ("Developer -
  generating code"). Click the pill to expand a popover with the
  full six-step pipeline trail. When a run ends, the pill briefly
  flashes green (completed) or red (failed) for ~4 seconds and
  then settles back to idle.
- **Centered composer** floats at the bottom of the chat, max-width
  760px. Gradient Send button with an arrow that slides right on
  hover; ghost-red Stop button that takes over during a run.
  Keyboard hints sit below: `Enter` to send, `Shift+Enter` for a
  newline, and `Cmd/Ctrl+Enter` also sends.
- **Slide-in preview drawer** from the right edge, 420px wide. It
  auto-opens when a changeset arrives and the toolbar toggle shows
  a red dot until you do. Click the toggle (or press Escape when
  no other surface is open) to close; the drawer minimizes to a
  floating chip at the bottom-right showing the change count, and
  clicking the chip reopens the drawer. The drawer state is
  persisted across reloads. On mobile the drawer becomes a
  full-screen modal with a dimming scrim.
- **Mode chips + tone banners + step trails** still anchor the
  visual language - mode chips use the four mode colors (auto,
  dev, plan, insights), banners use tone colors (success green,
  info blue, warn orange, danger red, neutral gray), and step
  trails share one dot + pulse vocabulary across the transcript
  and the preview deploy stream.

Screenshots (captured from a live session):

![Welcome state](images/alfred-welcome.png)
![Mid-run with status pill](images/alfred-working.png)
![Deployed changeset in drawer](images/alfred-deployed.png)

## The Interface

When you open `/app/alfred-chat`, you see a single-column chat. The
preview lives in a drawer that slides in from the right when it has
something to show.

```
┌──────────────────────────────────────────────────────────────┐
│ [<-] [A] Title · [ModeSwitcher]   [≡] [+ New] [...]          │ <- frosted topbar (48px)
├──────────────────────────────────────────────────────────────┤
│                                                              │
│          ┌──── ● Ready  OR  [mark] Developer...┐             │ <- floating status pill
│          └─────────────────────────────────────┘             │
│                                                              │
│   [user msg]                                                 │
│   [agent msg]                                                │
│   [agent-step]                                               │
│                                                              │
│                                                              │
│        ┌────────────────────────────────────┐                │
│        │ Composer (centered, max 760px)     │  [Send ->]     │ <- floating composer
│        │ Enter to send, Shift+Enter newline │                │
│        └────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────┘
                          ┌───────────────────────────────┐
                          │ Preview (slides in from right │
                          │  when a changeset arrives)    │
                          └───────────────────────────────┘
```

### Status pill (top of transcript)
- **Idle**: small green dot + **Ready**. Sits at ~75% opacity; brightens on hover.
- **Processing**: pulsing chat-gradient mark + bold agent name + live activity phrase (e.g. "Developer - generating code"). Click to expand a popover that shows the full six-step pipeline (Requirements -> Assessment -> Architecture -> Development -> Testing -> Deployment) with the current step highlighted.
- **Outcome**: briefly flashes green (**Completed**) or red (**Failed**) for ~4 seconds then returns to idle. The elapsed seconds counter sits to the right of the label during a run.
- **Basic mode**: the popover replaces the six-step pipeline with a "Basic mode" chip since the single-agent run has no visible phases.

### Preview drawer (right edge)
- **Auto-opens** when Alfred produces a changeset; the toolbar toggle (hamburger icon) shows a red dot until you see it.
- **Minimize** to a floating pill at the bottom-right showing the change count; click the pill to reopen at the exact scroll position.
- **Escape** closes the drawer (after the overflow menu and status popover, if those are open first).
- **Persisted** across reloads via localStorage. On mobile the drawer becomes a full-screen modal with a dimming scrim and focuses the close button for keyboard users.

### Stop a run

While a run is in flight the **Send** button is replaced with a **Stop** button. Clicking Stop sends a graceful cancel: the current agent phase completes, the pipeline exits cleanly, the conversation is marked **Cancelled**, and the chat shows a neutral "Run cancelled" system message. The WebSocket stays open so you can keep chatting in the same conversation. If the processing app is unreachable, the conversation is still marked Cancelled locally so the UI does not stay stuck on "In Progress".

### Resizable panels

The chat and preview panels scroll independently and can be resized by dragging the thin vertical bar between them. Your split is remembered across reloads. On narrow screens (below ~768px wide) the panels stack vertically and the resize handle is hidden.

### Refresh during a run

Refreshing the page at any point keeps everything you had on screen. The chat transcript, the current phase pipeline, the agent activity ticker, and the preview panel all rebuild from the server. What you see after a refresh:

- **Mid-run**: the phase pipeline picks up where it was; the ticker shows the last-known agent activity; the status bar reads "... is working...".
- **Awaiting review**: the Pending changeset is re-rendered with its Approve / Reject / Request Changes buttons.
- **After deploy**: the deployed changeset is shown read-only with a green "Deployed successfully" banner and a **Rollback** button (when rollback data is available).
- **After rollback**: a neutral "Deployment rolled back" banner replaces the Deployed banner.
- **After a deploy failure**: a red "Deploy failed - rolled back" banner lists the failed steps.
- **After Stop**: the conversation reads **Cancelled** with a neutral message; send a new prompt to continue in the same conversation.

### Left Panel
- **Conversation list** - Shows your past conversations with the first message as a summary, status badge, and relative time. Click to open.
- **Chat area** - When a conversation is open, shows the message thread and input box.

### Right Panel
- **Preview** - Empty until Alfred has something to show. During early phases, shows what Alfred is doing ("Gathering Requirements...", "Designing Solution..."). Once a changeset is ready, shows DocType field tables, script code, and permission grids.
- **Action buttons** - Appear when a changeset is ready for your review.

---

## Your First Conversation

### Step 1: Start

Open `/app/alfred-chat`. If this is your first time, you'll see a welcome screen with three example prompts. Click one, or click **"Start a Conversation"** and type your own request.

**Good prompts are specific:**
> "Create a DocType called Training Program with fields: program_name (Data, required), duration_days (Int), trainer (Link to Employee), and status (Select: Draft/Active/Completed)"

**Vague prompts trigger questions:**
> "I need something for HR training"
>
> Alfred will ask: "What specific aspect of HR training do you want to manage? Training programs, training requests, attendance tracking, or something else?"

**Conversational messages get conversational replies.** When the
three-mode orchestrator is enabled (`ALFRED_ORCHESTRATOR_ENABLED=1` on
the processing app), Alfred recognises greetings, thank-yous, and meta
questions and replies in kind without running the full SDLC pipeline:

> **You:** hi
>
> **Alfred:** Hi! I'm Alfred, your Frappe customization assistant. Tell me what you'd like to build, or ask about what's already on your site.

These conversational turns are fast (~5 seconds), don't consume agent
tokens, and never produce a changeset.

**You can also ask about what's already on your site.** If you ask a
read-only question, Alfred answers from your live site state using
read-only tools - no build, no approval, no changes:

> **You:** what DocTypes do I have in the HR module?
>
> **Alfred (Insights):** You have 18 DocTypes in the HR module: Employee, Leave Application, Attendance, Expense Claim, ... The Leave Application DocType is submittable and currently has 2 custom fields added on this site.

Insights-mode replies are markdown, usually a few sentences or a short
list, and complete in about 10-30 seconds. They're budget-capped at 5
tool calls per question so the assistant won't fan out into expensive
queries.

**You can ask for a plan before committing to a build.** If you want to
discuss the approach first, phrase it as a design question and Alfred
responds with a structured plan panel:

> **You:** how would we approach adding approval to Expense Claims?
>
> **Alfred (Plan panel):**
> Title: *Approval workflow for Expense Claims*
> Summary: Add a 2-step approval with manager, then finance.
> Steps:
>   1. Create Workflow 'Expense Claim Approval'
>   2. Create Notification for the approver
> Doctypes touched: Workflow, Notification
> Open questions: Who approves when the manager is absent?
> [Refine] [Approve and Build]

Click **Refine** to tweak the plan with another prompt, or **Approve &
Build** to promote it straight to Dev mode. Approved plans are injected
into the Developer agent's context as an explicit spec so the build
follows your plan exactly.

**You can also force a specific mode via the switcher** in the chat
header (Auto / Dev / Plan / Insights). Auto is the default and lets
Alfred decide; the other three force the mode for every prompt on that
conversation. Your pick is remembered per conversation so switching
away and back doesn't reset it. Use the forced modes when Alfred keeps
mis-routing - e.g. force Insights to explore your site without any
build risk, or force Dev if you want to skip the planning dance and
go straight to code.

Build requests ("add a priority field to Sales Order") still run the
full 6-agent pipeline. See
[how-alfred-works.md#chat-modes-and-the-orchestrator](how-alfred-works.md#chat-modes-and-the-orchestrator)
for the full details.

### Step 2: Alfred Gathers Requirements

The **Requirement Analyst** agent processes your request. You'll see:
- Status bar shows "Step 1/6 - Requirement Analyst is working..." with a pulsing yellow dot
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

The **QA Validator** (plus a dedicated **pre-preview dry-run** against your live site) checks everything:
- Python syntax in Server Scripts (`compile()` check)
- JavaScript syntax in Client Scripts
- Jinja syntax in Notification subjects and message templates
- Field types are valid Frappe types
- Naming conflicts don't exist
- Permission checks are present in scripts
- Deployment order is correct (dependencies first)
- **Dry-run insert with savepoint rollback** - every proposed document is actually inserted into your database in a transaction, then immediately rolled back. This catches errors that only surface at insert time (missing mandatory fields, unresolved Link targets, etc.) **without** leaving any trace in your data.

**If validation fails**, Alfred automatically asks the Developer to fix the issues and runs the dry-run again (once). If it still fails, the preview panel shows the concrete issues and you can:
- Click **Deploy Anyway** (if you know the error is a false positive)
- Click **Request Changes** and tell Alfred what to fix
- Click **Reject** and start over

### Step 6: Review & Approve

The preview panel shows the complete changeset:

**Validation banner** at the top:
- ✓ **Validated - ready to deploy** - dry-run passed, deploy is safe
- ⚠ **N validation issue(s) found - review before deploying** - shows a list of critical/warning issues, Approve button relabels to "Deploy Anyway"

**DocType preview** - shows module, naming rule, submittable/tree/single flags,
and a field table:
| Field | Type | Label | Options | Required |
|-------|------|-------|---------|----------|
| program_name | Data | Program Name | | Yes |
| duration_days | Int | Duration (Days) | | |
| trainer | Link | Trainer | Employee | |
| status | Select | Status | Draft\nActive\nCompleted | |

**Notification preview** - document type, event, channel, subject with Jinja
template rendering, recipient summary (field/role/cc), `enabled` flag, and
the full HTML message body.

**Custom Field preview** - target DocType, field name (as `code`), type,
label, options, default, insert_after, required flag, list-view visibility.

**Server Script preview** - reference DocType, script type, doctype event,
cron / api_method / event_frequency for scheduled scripts, disabled flag,
and the full Python source in a syntax-highlighted block.

**Workflow preview** - workflow name, target DocType, state field, active
flag, and **two additional tables**:

*States:*
| State | Doc Status | Allow Edit | Update Field |
|-------|-----------|-----------|-------------|
| Draft | Draft (0) | Employee, Leave Approver | |
| Pending Approval | Submitted (1) | Leave Approver | |
| Approved | Submitted (1) | | |

*Transitions:*
| From State | Action | To State | Allowed | Condition |
|-----------|--------|----------|---------|-----------|
| Draft | Submit | Pending Approval | Employee | |
| Pending Approval | Approve | Approved | Leave Approver | |

**Permission preview:**
| Role | Read | Write | Create | Delete |
|------|------|-------|--------|--------|
| System Manager | Yes | Yes | Yes | Yes |

Below the preview, a summary shows: "3 operation(s) will be applied to your site"

**Reflection banner** (if the minimality step is enabled and dropped anything):
you'll see a purple "Dropped N item(s) as not strictly needed" note listing
each trimmed item and why Alfred thought it wasn't in your original request.
Nothing you asked for is ever removed - only extras the agent volunteered.

Three buttons appear:

- **Approve & Deploy** - A confirmation dialog shows exactly what will be created. Click "Yes" to deploy.
- **Request Changes** - The input box focuses with placeholder "What would you like to change?" Type what you want different (e.g., "Add a description field and make status required").
- **Reject** - Cancels the changeset. You can start over with a new prompt.

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
- **Continue the conversation with follow-up requests** - Alfred remembers
  every DocType, field, script, and notification it built earlier in this
  chat, plus the clarifications you provided. So after deploying a Training
  Program DocType you can say *"now add a description field to that DocType"*
  and Alfred will know "that" means Training Program without you spelling it
  out. The memory lasts for the lifetime of the conversation.
- Start a new conversation for a different customization (memory does not
  carry across conversations - each new chat is a fresh slate)

---

## Managing Conversations

### Conversation List

Your past conversations are listed with:
- **Summary** - First message you sent (truncated to 80 characters)
- **Status badge** - Color-coded current status
- **Time** - When last active ("2 minutes ago", "Yesterday at 3:15 PM")

### Conversation Statuses

| Status | Color | Meaning | What to Do |
|--------|-------|---------|------------|
| Open | Blue | Created but no messages yet | Send your first prompt |
| In Progress | Orange | Agents are actively working | Wait - or answer if asked |
| Awaiting Input | Yellow | Alfred asked a question | Answer the question |
| Completed | Green | All done, changes deployed | Nothing - or ask a follow-up |
| Escalated | Red | Too complex for AI | A human developer will handle it |
| Failed | Red | Something went wrong | Read the error, retry or start over |
| Stale | Gray | Inactive for 24+ hours | Open and continue, or start a new one |
| Cancelled | Gray | You clicked Stop mid-run | Send a new prompt to continue in the same chat |

### Finding a Conversation

Conversations show the first message as a summary. If you have many, scroll through the list - they're sorted by most recent activity.

---

## When Things Go Wrong

### Error Messages

Alfred translates technical errors into plain language:

| You See | What It Means |
|---------|--------------|
| "There was a problem with the data format" | The generated code had a structural issue - Alfred will retry |
| "You don't have permission for this operation" | Your Frappe role can't create this type of customization |
| "A document with this name already exists" | The DocType or script name conflicts with something on your site |
| "The operation took too long" | LLM or processing timeout - try again |
| "Could not connect to the processing service" | The processing app is down - contact your admin |
| "Your message was flagged by the security filter" | Your prompt matched a security pattern - rephrase it |

Every error message has:
- A **human-readable explanation** at the top
- An expandable **"Technical details"** section for admins
- A **"Retry"** button that resends your last message

### Escalation

If Alfred can't handle your request (too complex, repeated failures, ambiguous after 3 clarification attempts), the conversation is **escalated**:

1. Status changes to "Escalated"
2. System Managers receive an in-app notification and email
3. A human developer reviews the conversation and either:
   - **Takes over** - completes the work manually
   - **Returns to Alfred** - with clarified requirements for the AI to retry

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
  - **Ollama** (self-hosted) - Everything stays on your server. Nothing leaves your network.
  - **Cloud providers** (Claude, GPT, Gemini) - Conversation messages are sent to the provider's API. Your site schema is included for context. No document data is sent.
- **What's stored**: Conversations, messages, changesets, and audit logs are stored in your Frappe site's database. They can be viewed at `/app/alfred-conversation`.
- **Audit trail**: Every action Alfred takes is logged in the Alfred Audit Log with before/after state snapshots.
