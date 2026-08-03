# EVA API Reference

Status: canonical overview. The generated schema at `/openapi.json` is the
authority for request models and the complete endpoint list.

Base URL: `http://127.0.0.1:8000`

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`

## Authentication

Set `EVA_API_TOKEN` and send it in `X-API-Token` for protected HTTP routes.
When no token is configured, read-only endpoints and chat remain available in
development, while other state-changing endpoints fail closed.

Public operational endpoints include liveness, readiness, metrics, API docs,
and the GitHub webhook. The webhook uses its HMAC secret instead. WebSocket
authentication accepts `X-API-Token` or `?token=...` when a token is configured.

## Chat Contracts

### Queue A Message

`POST /api/chat`

```json
{
  "text": "Summarize the current EVA architecture"
}
```

The endpoint returns immediately:

```json
{
  "accepted": true,
  "task_id": "correlation-id",
  "event_id": "event-id",
  "message": "event queued; listen on WS chat_reply or poll /api/chat/result/{task_id}"
}
```

Receive the result from the WebSocket `chat_reply` channel or poll:

`GET /api/chat/result/{task_id}`

A pending result returns HTTP 202. A completed result is consumed from the
registry and returns `reply`, `selected_agent`, `loop_id`, and `duration_ms`.

### Synchronous Compatibility

`POST /api/chat/sync`

This publishes the same event but blocks for `EVA_REQUEST_TIMEOUT_SEC`. It
returns HTTP 202 if processing continues beyond that window. Prefer the queued
endpoint for applications.

### Server-Sent Events

`POST /api/chat/stream`

This runs the full cognition pipeline and returns `text/event-stream`. Tokens
arrive as `data:` frames, followed by `data: [DONE]`. The implementation uses
the runtime WebSocket manager internally.

## WebSocket

Connect to `ws://127.0.0.1:8000/ws`. Send `ping` to receive a `pong` payload.

Important channels:

| Channel | Purpose |
| --- | --- |
| `chat_token` | Incremental model/agent output with `task_id` |
| `chat_reply` | Final normalized response with `task_id` |
| `policy_state` | Policy transition updates |
| `entity_created` | World-graph entity extraction |
| `audit_event` | Executor and capability audit updates |
| `system_state` | Runtime state updates |

## Endpoint Families

### Runtime And Health

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/state` | Current focus, tasks, queue, agent, loop, and memory summary |
| GET | `/health/live` | Process liveness |
| GET | `/health/ready` | Component readiness |
| GET | `/health/diagnostic` | Detailed diagnostic report |
| GET | `/health/summary` | Compact health summary |
| POST | `/health/recover` | Explicit recovery action |
| GET | `/metrics` | Application metrics |
| GET | `/metrics/prometheus` | Prometheus text format |
| GET | `/api/config` | Redacted effective configuration and runtime worker stats |
| GET | `/api/config/defaults` | Documented defaults |

### Cognition, Policy, And Persona

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/policy/state` | Current policy state and optional detail |
| POST | `/api/policy/transition` | Explicit allowed state transition |
| GET | `/api/policy/history` | Transition history |
| GET | `/api/persona/active` | Active persona |
| POST | `/api/persona/update` | Constrained persona update |
| GET | `/api/persona/stats` | Persona stability/update statistics |
| GET | `/api/proactive/state` | Proactive engine state |
| POST | `/api/debug/maintenance/trigger` | Run maintenance now |
| GET | `/api/scheduler/jobs` | Scheduler jobs |
| GET | `/api/scheduler/stats` | Scheduler statistics |

Self-model and constitution endpoints are grouped under `/api/mvsc/self/*` and
`/api/constitution/*`. Inspect OpenAPI for their current schemas.

### Agents And Tools

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/agents` | Registered agents |
| POST | `/api/agents/register` | Register a constrained LLM-backed agent |
| DELETE | `/api/agents/{name}` | Remove a non-built-in agent |
| GET | `/api/agents/stats` | Agent and executor mapping summary |
| GET | `/api/tools` | Available tools and schemas |
| POST | `/api/tools/call` | Invoke a permitted tool |
| GET | `/api/tools/stats` | Tool runtime statistics |
| GET | `/api/tools/usage` | Tool usage history |

Runtime registration accepts an agent name, description, and one allowed task
kind. It does not load arbitrary Python code. Built-in agents cannot be removed.

### Executors And Audit

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/executors` | Registered executors |
| POST | `/api/executors/file/read` | Guarded file read |
| POST | `/api/executors/file/write` | Guarded file write |
| POST | `/api/executors/file/list` | Guarded directory list |
| POST | `/api/executors/code/execute` | Opt-in subprocess code execution |
| GET | `/api/executors/audit` | Filtered audit records |
| GET | `/api/executors/audit/replay` | Task audit timeline |
| GET | `/api/executors/audit/verify` | Audit integrity verification |
| GET | `/api/executors/stats` | Executor statistics |

File operations remain restricted to configured roots. Code execution requires
authentication, capability policy, and `EVA_ENABLE_CODE_TOOL=true`. The current
subprocess executor is not equivalent to a hardened container sandbox.

### Memory, Events, And Snapshots

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/memory/recent` | Recent episodic memory |
| GET | `/api/memory/tiers` | S1-S5 tier summary |
| GET | `/api/memory/working` | Working memory |
| GET | `/api/memory/longterm` | Long-term memory |
| GET | `/api/memory/world` | World entities and relations |
| GET/POST | `/api/memory/search` | Memory search surfaces |
| GET | `/api/memory/compaction/stats` | Compaction status |
| GET | `/api/memory/db-stats` | Storage statistics |
| GET | `/api/events/recent` | Recent normalized events |
| GET | `/api/events/stats` | Event statistics |
| GET | `/api/debug/trace` | Cognition traces |
| GET | `/api/debug/snapshot` | Latest snapshot |
| POST | `/api/debug/snapshot/save` | Save now |
| GET | `/api/debug/snapshot/list` | Available snapshots |
| GET | `/api/debug/snapshot/verify` | Verify snapshot integrity |

Conversation import/export and memory explorer endpoints are also available
under `/api/conversation/*` and `/api/memory/entry|entries/*`.

### GitHub

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/webhooks/github` | Receive signed GitHub webhook deliveries |
| GET | `/api/github/status` | Connector/poller status |

The connector normalizes GitHub input into events. It must not directly execute
an agent. Use a webhook secret and repository allowlist in non-local deployments.

### Experimental MVSC

Endpoints under `/api/mvsc/*` expose state, ablation, lifecycle, integrity,
trace, cache, vector, and observability data for the optional experimental
pipeline. They are not part of the stable compatibility contract and may change
while `EVA_ENABLE_MVSC_PIPELINE` remains disabled by default.

## Agent Routing

The planner recognizes explicit commands and intent keywords:

| Input | Default agent |
| --- | --- |
| General text | `chat_agent` |
| `/search`, `search:`, `find:`, `lookup:` | `search_agent` |
| `/code`, `code:`, `inspect:`, `review:` | `coding_agent` |
| Document summary intent | `docs_agent` |

All paths still pass through event normalization, cognition, policy, routing,
worker execution, result integration, and memory/trace writeback.

## Common Status Codes

| Code | Meaning |
| --- | --- |
| 200 | Completed request |
| 202 | Accepted or still processing |
| 400/422 | Invalid request or transition |
| 401/403 | Missing token, invalid token, or denied capability |
| 404 | Unknown task/resource/agent |
| 409 | Duplicate runtime registration or conflicting state |
| 429 | Rate limit exceeded |
| 503 | Required runtime component unavailable |
