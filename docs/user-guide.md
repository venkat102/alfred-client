# Alfred User Guide

## What is Alfred?

Alfred is an AI-powered assistant that builds Frappe customizations through conversation. Tell Alfred what you need — a new DocType, workflow, report, or custom field — and it designs, generates, validates, and deploys the solution to your site.

### What Alfred Can Do
- Create new DocTypes with fields, permissions, and naming rules
- Add custom fields to existing DocTypes
- Create Server Scripts (validation, automation, API endpoints)
- Create Client Scripts (form customization, dynamic filters)
- Build workflows with approval chains
- Generate reports and notifications
- Set up print formats

### What Alfred Cannot Do
- Modify core Frappe/ERPNext source code
- Run bench commands or shell operations
- Access the file system directly
- Make changes requiring app-level Python files (hooks.py, etc.)
- Create features that don't exist in Frappe's customization framework

---

## Getting Started

### 1. Access Alfred
Navigate to `/app/alfred` in your Frappe site. You must have an allowed role configured in Alfred Settings.

### 2. Start a Conversation
Click **"New Conversation"** and type your request. Be specific:

**Good:** "Create a DocType called 'Training Program' with fields: program_name (Data, required), duration_days (Int), trainer (Link to Employee), and status (Select: Draft/Active/Completed)"

**Too vague:** "Make something for training" (Alfred will ask clarifying questions)

### 3. The Agent Pipeline
Alfred processes your request through 6 specialist agents:

| Phase | Agent | What It Does |
|-------|-------|-------------|
| 1. Requirements | Requirement Analyst | Gathers and structures your requirements |
| 2. Assessment | Feasibility Assessor | Checks permissions and detects conflicts |
| 3. Architecture | Solution Architect | Designs the technical solution |
| 4. Development | Frappe Developer | Generates document definitions and code |
| 5. Testing | QA Validator | Validates syntax, permissions, naming |
| 6. Deployment | Deployment Specialist | Deploys with your approval |

### 4. Review the Preview
The right panel shows what Alfred proposes:
- **DocType fields** in a table format
- **Scripts** with syntax highlighting
- **Permissions** as a role x permission grid
- **Workflows** with states and transitions

### 5. Approve or Modify
- **Approve & Deploy** — deploys immediately to your site
- **Request Changes** — tell Alfred what to modify
- **Reject** — cancels the changeset

---

## Message Types

| Type | Appearance | What It Means |
|------|-----------|--------------|
| Your message | Blue bubble (right) | What you typed |
| Agent response | Gray bubble (left) | Agent's answer with agent badge |
| Question | Gray with option buttons | Agent needs your input — click an option or type |
| Status | Centered, subtle | Pipeline progress update |
| Error | Red highlight | Something went wrong |

---

## FAQ

**Q: Can Alfred modify existing DocTypes like Sales Invoice?**
A: Alfred can add custom fields to existing DocTypes but cannot modify core fields or rename them.

**Q: What happens if deployment fails?**
A: All changes are automatically rolled back. No partial changes are left on your site.

**Q: Can I undo a deployment?**
A: Yes, go to the Alfred Changeset record and click Rollback. DocTypes with user data will be preserved.

**Q: Which LLM does Alfred use?**
A: Configured in Alfred Settings. Supports Ollama (local/free), Anthropic Claude, OpenAI GPT, Google Gemini, and AWS Bedrock.

**Q: Can multiple users use Alfred simultaneously?**
A: Yes. Each user has their own conversations and permission scope.

**Q: What if Alfred gets stuck or gives wrong answers?**
A: The conversation is automatically escalated to a human developer after 3 failed attempts.

**Q: Is my data sent to external AI services?**
A: Only the conversation messages and site schema (DocType field definitions) are sent to the LLM. With Ollama, everything runs locally.

**Q: How do I add more roles that can use Alfred?**
A: Go to Alfred Settings > Access Control > Allowed Roles and add the roles.

**Q: What's the difference between the Alfred and Alfred Settings modules?**
A: "Alfred" is where AI-created DocTypes land. "Alfred Settings" contains Alfred's own configuration DocTypes.

**Q: Can I see what Alfred changed on my site?**
A: Yes, check the Alfred Audit Log for a complete history of every action taken.
