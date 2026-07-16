# EVA API Reference

All endpoints grouped by architectural layer. Base: `http://localhost:8000`.

Swagger UI: `/docs` | ReDoc: `/redoc` | OpenAPI: `/openapi.json`

---

## 1. Cognition Core

### Chat

`POST /api/chat`

Send a message to EVA. Returns agent reply with metadata.

**Request**:

```json
{
  "text": "Fix the login bug"
}
```

**Response** (200):

```json
{
  "accepted": true,
  "completed": true,
  "event_id": "...",
  "correlation_id": "...",
  "reply": "[EVA] Understood. Checking active tasks...",
  "selected_agent": "chat_agent",
  "loop_id": "loop_...",
  "duration_ms": 12
}
```

Timeout returns 202 with `completed: false`.

### System State

`GET /api/state`

Full system state snapshot including focus, mode, pending events/results, last agent/loop.

---

## 2. Policy Layer

`GET /api/policy/state`

Current policy state. Query `?detail=true` for full token list + state history.

```json
{
  "state": "dormant",
  "state_since_seconds": 120,
  "history_size": 3,
  "active_tokens": 0
}
```

`POST /api/policy/transition`

Manual state transition. Allowed triggers: `manual_intervention`, `autonomy_granted`, `user_revoke`.

```json
{ "trigger": "manual_intervention" }
```

---

## 3. Memory Layer

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/memory/recent?limit=20` | Recent episodic memories |
| GET | `/api/memory/tiers` | S1-S5 tier statistics |
| GET | `/api/memory/working?limit=50` | S2 working memory entries |
| GET | `/api/memory/longterm?limit=50&category=general` | S3 long-term memory (optional category filter) |
| GET | `/api/memory/world?entity_type=task&limit=100` | S4 world model entities + edges |
| GET | `/api/events/recent?limit=20` | Recent raw events |
| GET | `/api/debug/trace?limit=20` | Recent cognition loop traces |

---

## 4. Executor Layer

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/executors` | List registered executors |
| GET | `/api/executors/audit?type=file&limit=50&status=error` | Query executor audit log |
| GET | `/api/executors/audit/replay?task_id=...&limit=20` | Timeline-style audit replay |
| POST | `/api/executors/file/read` | Read file (token + path whitelist) |
| POST | `/api/executors/file/write` | Write file |
| POST | `/api/executors/file/list` | List directory |
| POST | `/api/executors/code/execute` | Execute sandboxed code (python/bash) |

**File read example**:

```json
POST /api/executors/file/read
{
  "path": "/data/eva/workspace/notes.txt",
  "token_id": "tok_abc123",
  "task_id": "task_xyz"
}
```

**Code execute example**:

```json
POST /api/executors/code/execute
{
  "code": "print(sum(range(100)))",
  "language": "python",
  "token_id": "tok_abc123"
}
```

---

## 5. Persona Layer

`GET /api/persona/active`

Active persona profile (name, role, tone, constraints, value weights).

`POST /api/persona/update`

Patch persona fields. Accepted keys: `name`, `role_definition`, `tone_style`, `hard_constraints`, `soft_preferences`, `value_weights`, `confidence`.

---

## 6. Proactive Engine

`GET /api/proactive/state`

Proactive engine state: last user message, last reminder, stagnation status.

`POST /api/debug/maintenance/trigger`

Manually trigger a maintenance cycle (memory decay + compaction).

---

## 7. Snapshot & Debug

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/debug/snapshot` | Load latest snapshot |
| POST | `/api/debug/snapshot/save` | Force snapshot save |
| POST | `/api/debug/snapshot/restore` | Restore state from snapshot |

---

## 8. Health & Diagnostics

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health/live` | Liveness: `{"status":"alive"}` |
| GET | `/health/ready` | Readiness: per-component boolean report |
| GET | `/health/diagnostic` | Full diagnostic: DB, snapshot, config, memory, runtime |
| GET | `/health/ws` | WebSocket connection statistics |
| POST | `/health/recover` | Execute recovery action |

**Recovery actions**:

```json
POST /health/recover
{ "action": "reset_policy" }
```
Actions: `reset_policy`, `clear_registry`, `rebuild_db`, `rebuild_snapshot`.

---

## 9. WebSocket

`ws://localhost:8000/ws` (or `wss://` for HTTPS)

Optional: `?channel=policy_state|entity_created|audit_event|system_state`

Kept alive via `ping` → `pong` protocol.

### Event Types

| Channel | Payload | Trigger |
|---------|---------|---------|
| `policy_state` | `{"state_machine":{"current":"quarantined"},...}` | State changes, quarantine |
| `entity_created` | `{"type":"task","name":"Fix bug","properties":{}}` | Entity extraction from agent reply |
| `audit_event` | `{"executor_type":"file","action":"read","status":"success"}` | Executor audit entries |
| `system_state` | Full system state snapshot | Periodic or on change |

---

## 10. Agent Routing

Agents are selected by command prefix or keyword match in the Planner:

| Prefix / Keyword | Agent | Description |
|------------------|-------|-------------|
| (default) | `chat_agent` | General conversation (with LLM if API key configured) |
| `/search`, `search:`, `find:`, `lookup:` | `search_agent` | Full-text search in local files |
| `/code`, `code:`, `inspect:`, `review:` | `coding_agent` | Code file analysis |
| `summary`, `docs`, `document`, `summarize` | `docs_agent` | Documentation processing |

---

## 11. Scheduler

`GET /api/scheduler/jobs`

List scheduled APScheduler jobs with next run time.

`GET /api/agents`

List registered agent names.

---

## Error Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 202 | Accepted (chat timeout — event still processing) |
| 400 | Bad request (missing fields, invalid trigger) |
| 404 | Not found (unknown executor, agent) |
| 503 | Unavailable (component not initialized) |
