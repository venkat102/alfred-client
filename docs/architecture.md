# Alfred Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Customer's Frappe Site                        │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────┐   │
│  │  Chat UI      │  │  MCP Server  │  │  Deployment Engine      │   │
│  │  /app/alfred   │  │  (9 tools)   │  │  (changeset executor)   │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────────┘   │
│         │ Socket.IO        │ JSON-RPC             │                  │
│  ┌──────┴──────────────────┴──────────────────────┴──────────────┐  │
│  │              WebSocket Client (outbound)                       │  │
│  │              alfred_client app                                 │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
└─────────────────────────────┼───────────────────────────────────────┘
                              │ WSS (client-initiated)
┌─────────────────────────────┼───────────────────────────────────────┐
│                Processing App (FastAPI + Docker)                     │
│  ┌──────────────────────────┴────────────────────────────────────┐  │
│  │              API Gateway (JWT auth + rate limiting)            │  │
│  └──────────────────────────┬────────────────────────────────────┘  │
│  ┌──────────┐  ┌────────────┴───────────────┐  ┌────────────────┐  │
│  │  Prompt   │  │  CrewAI Orchestrator       │  │  MCP Client    │  │
│  │  Defense  │→ │  (6 agents + manager)       │←→│  (9 tools)    │  │
│  └──────────┘  └────────────┬───────────────┘  └────────────────┘  │
│                             │                                       │
│  ┌──────────────────────────┴────────────────────────────────────┐  │
│  │              Redis (state store + event streams)               │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                              │ HTTPS
┌─────────────────────────────┼───────────────────────────────────────┐
│                Admin Portal (Frappe site)                            │
│  ┌──────────────┐  ┌───────┴──────┐  ┌──────────────────────────┐  │
│  │  Customers   │  │  Usage API   │  │  Billing / Subscriptions  │  │
│  │  & Plans     │  │  (3 endpoints)│  │  (Frappe Payments)       │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
│                alfred_admin app                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Agent Pipeline

```
User Prompt
    │
    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Requirement  │────▶│  Assessment  │────▶│  Architect   │
│  Analyst      │     │  Assessor    │     │  Designer    │
└──────────────┘     └──────┬───────┘     └──────┬───────┘
                            │                     │
                     (if blocked)          ┌──────▼───────┐
                            │              │  Developer   │
                     ┌──────▼───────┐      │  Generator   │
                     │  Escalation  │      └──────┬───────┘
                     │  to Human    │             │
                     └──────────────┘      ┌──────▼───────┐
                                           │  Tester      │──┐
                                           │  Validator   │  │ (max 3 loops)
                                           └──────┬───────┘  │
                                                  │    ▲      │
                                                  │    └──────┘
                                           ┌──────▼───────┐
                                           │  Deployer    │
                                           │  (approval)  │
                                           └──────┬───────┘
                                                  │
                                                  ▼
                                           Deployed to Site
```

## Permission Model (5 Layers)

```
Layer 1: UI Access       → validate_alfred_access() on page load
Layer 2: API Auth        → API key + JWT on WebSocket handshake
Layer 3: Assessment      → Deterministic permission matrix check
Layer 4: Generated Perms → Tester validates generated DocType permissions
Layer 5: Deployment      → frappe.has_permission() on every operation
```

## Data Flow

```
Alfred Settings (config)
    │
    ├──▶ WebSocket Client ──▶ Processing App
    │         │                     │
    │         │◀── Agent events ────┘
    │         │
    │         ├──▶ frappe.publish_realtime() ──▶ Browser (Socket.IO)
    │         │
    │         ├──▶ Alfred Message (chat history)
    │         │
    │         └──▶ Alfred Changeset (proposed changes)
    │                   │
    │            (user approval)
    │                   │
    │                   ▼
    │            Deployment Engine
    │                   │
    │                   ├──▶ Created DocTypes/Scripts/Workflows
    │                   ├──▶ Alfred Audit Log (every step)
    │                   └──▶ Rollback Data (for undo)
    │
    └──▶ Alfred Conversation (session tracking)
```

## DocType Relationships

```
Alfred Settings (Single)
    └── Alfred Allowed Role (child table)

Alfred Conversation
    ├── Alfred Message (1:many via Link)
    ├── Alfred Changeset (1:many via Link)
    ├── Alfred Audit Log (1:many via Link)
    └── Alfred Created Document (child table)

Alfred Customer (Admin Portal)
    ├── Alfred Subscription (1:many via Link)
    └── Alfred Usage Log (1:many via Link)

Alfred Plan (Admin Portal)
    └── Alfred Plan Feature (child table)
```
