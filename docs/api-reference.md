# EVA API Reference

Chat endpoints accept optional `schema_version` (only the string `"1.0"`) and
`source_event_id` (1–128 characters). Omitted fields preserve normal chat behavior.
Unknown explicit versions return 422 before admission or receipt reservation.
Supplying the same source occurrence ID yields a stable event ID across delivery
methods; each call still has its own task/receipt ID. This does **not** provide
idempotent execution. Do not reuse an occurrence ID for different messages.

Signed GitHub webhooks with unsupported event types or a non-object payload now
return 422 without publishing or recording delivery deduplication. Supported types
are `push`, `pull_request`, `issues`, and `workflow_run`. Unknown events no longer
fall back to push. See [EVT-01](versioned-events-evt01.md) for contract and migration details.

When a chat request cannot enter the bounded event queue, async, sync and SSE
chat endpoints return HTTP 503 with `accepted=false` and `error=event_queue_full`.
The request's result reservation is removed. Retry after the
reported interval. Minimal Brain also reports post-admission queue rejections
through the existing result channel; its live state is available under
`GET /api/state` → `minimal_brain` when enabled. See
[Minimal Brain v0.1](minimal-brain-v01.md).

Full result-registry capacity returns 503 with `error=result_capacity_full` before
publication. Admitted user/correlated events cannot be evicted by background ticks.
See [EVT-00](request-receipts-evt00.md) for the complete receipt lifecycle.

Closing ingress returns `error=runtime_stopping`; a failed or unavailable
selected consumer returns `error=runtime_unavailable`. The same admission
contract applies to GitHub webhooks and manual maintenance events. Rejected
GitHub deliveries can retry with the same delivery ID.

`GET /api/runtime` returns versioned runtime observations: mode, phase,
accepting_events, consumer_running, execution_idle, event/consumer/processor/
worker statistics, and shutdown progress/errors. Minimal mode adds per-node
timing and failure observations under `consumer.nodes`. `/metrics` includes
the same `runtime` object. Prometheus exposes `eva_runtime_running`,
`eva_runtime_accepting_events` and `eva_runtime_execution_idle` gauges.

`/api/runtime` additionally reports actual `consumer_type` and `processor_type`,
requested feature flags under `requested`, and MVSC attachment under
`extensions.mvsc` (`requested`, `status`, `attached`, `is_http_consumer`). MVSC
attachment leaves the HTTP consumer in legacy mode; it is not pipeline activation.
`/api/state` and `/health/ready` report the same MVSC attachment status, replacing
the old inferred `enabled/tick` block. `/health/summary.cognition` reports actual
`runtime_mode/phase` instead of an unmeasured experimental tick. `/api/config`
retains `mvsc.enabled` as a configuration flag only. `/api/config/defaults` returns
declared defaults independent of environment overrides. See [BASE-02](runtime-baseline-base02.md).

`GET /api/chat/result/{task_id}` adds `timing`: `admission_to_execution_ms`,
`admission_to_terminal_ms`, and `waiting_age_ms`. All use the registry's monotonic
clock. Unobserved/inapplicable boundaries are null. Admission-to-execution includes
all scheduling waits before the shared processor claims a request.

`POST /health/recover` with `action=rebuild_snapshot` now uses the normal
complete snapshot serializer, preserving the existing persona, self-model,
memory and optional cognitive state fields.

Status: canonical overview. The generated schema at `/openapi.json` is the
authority for request models and the complete endpoint list.

Base URL: `http://127.0.0.1:8000`

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI JSON: `/openapi.json`

## Business goals (opt-in)

Requires `EVA_ENABLE_BUSINESS_GOALS=true` and SQLite; disabled routes return 503.
Uses the same API authentication as other protected routes.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/goals` | Register description and checks.* success_conditions against a known task_id; optional timezone-aware deadline |
| GET | `/api/goals/graph` | Read-only persisted goal/receipt/check projection; limit 1–40 (default 40), offset ≥0, q ≤200 characters; newest registrations first, latest 5 checks per goal |
| GET | `/api/goals/{goal_id}/history` | Read-only verification history; limit 1–50 (default 20), offset ≥0; newest checks first, no receipt reconciliation or file reads |
| GET | `/api/goals/{goal_id}/episode` | Read-only existing source-event Episode reference in the same EVA SQLite database; available/not_recorded/unavailable; no reconciliation or action replay |
| GET | `/api/goals/{goal_id}` | Read persisted state and reconcile a retained server receipt; may persist expiry/reconciliation |
| GET | `/api/goals/by-task/{task_id}` | Find the single goal bound to a task, including after a creation retry conflict |
| POST | `/api/goals/{goal_id}/cancel` | Version-checked tracking cancellation via expected_version; does not cancel actions |
| POST | `/api/goals/{goal_id}/verify` | expected_version only; server reads files from the immutable creation-time verification spec |

Unknown goal/task: 404. Duplicate task binding or state/version conflict: 409.
Malformed/extra fields: 422. Storage unavailable: 503. If creation returns 503
after its initial commit, look up by task ID before retrying. A successful request
leaves the goal pending_verification; no supplied model statement or client field
can complete it. New goals can carry a workspace_files_sha256_v1 verification spec
(1–16 relative paths with expected SHA-256); its only supported success condition
is checks.files_match equals true. The verify endpoint can complete that goal from
server file reads after a succeeded processing receipt. Missing/mismatched files
stay pending; read errors are unknown. Results are historical samples, not a live
filesystem guarantee or proof of code quality. Spec/condition conflicts return 409;
verify requests with posted observations or replacement specs return 422. The graph's
“目标与检查” view shows distinct goal, processing receipt, file-check and existing Episode nodes.
Graph reads use one SQLite read snapshot, never reconcile receipts, expire goals or
read files, and return `Cache-Control: no-store`. Counts describe the current page;
`scope.matched_goals` and `next_offset` support goal pagination. Each goal node carries
`verification_count` and `history_truncated`; only the latest five checks are drawn.
The goal detail can request earlier checks through the history endpoint; this also uses
one SQLite read snapshot and does not advance goal version or status. Pagination can shift
between requests as new goals arrive. See
[GOL-01](business-goals-gol01.md) for persistence and recovery boundaries.

The full graph UI provides a file-goal registration form using the existing POST
contract. It binds an admitted task ID and fixed file hashes, and converts an
optional device-local deadline to UTC. Registration does not run verification.
An ambiguous POST result locks further submissions until explicit by-task lookup;
that lookup can reconcile receipts and expiry, unlike the read-only graph route.
The independent memory preview disables registration. Drafts and submission locks
are page-local; after reloading, query the original task ID before submitting again.

Episode references validate stored row/JSON identities against the goal's source event.
Missing storage or event records stay not_recorded; malformed, oversized, conflicting or
unreadable records are unavailable and do not create graph nodes. The raw record limit is
64 KiB UTF-8; the structural projection shows at most 16 actions, bounded version/receipt
fields and explicit truncation flags, excluding raw observations, metadata and model text.
The graph can show at most 320 nodes / 280 edges per page including Episodes. Inspection
does not expire goals, replay external actions or promote execution success to goal completion.
`EVA_ENABLE_PROCESSING_EPISODES=true` optionally records started processing in that
SQLite database under either legacy or Minimal mode; it is independent of the goal
feature and defaults to false. `/api/runtime` exposes requested/attached state. Only
sealed rows are projected. Schema v2 includes actual world snapshot hashes with null
revisions and whitelisted completion/reference kinds; the UI labels recovery results
unknown and the timestamp as sealing time. Existing v1 revisions remain supported.
Episode sealing, compact terminal summaries and goal-notification outbox commit in
one transaction; goal delivery is idempotent and resumes before startup goal recovery.
Explicit reconciliation of existing goals falls back to validated summaries when the
in-memory receipt is missing (corruption returns 503); graph/history/reference reads
remain read-only. Registration still requires a current registered task, and chat
polling/SSE cannot restore reply text from a summary after restart. `/api/runtime`
includes pending notifications and local delivery-error counts. The recorder does not
provide a transaction spanning world/memory/external effects.

The Episode projection additionally includes `tool_receipts.status` and bounded receipt
observations for tool actions among the first 16 projected actions. Registry/Executor
entries in the agent's execution thread persist intent before invoking the handler;
successful returns are completed, error/exception observations are unknown. Seal-time
unknown receipts have no finish time, cannot be upgraded by a late return, and block
new calls after sealing. An unresolved matching tool/argument hash blocks repeat calls
within that Episode. Rows are checked against Episode action identities; missing or
invalid tool receipts preserve the Episode with explicit not_recorded/unavailable status.
Nested tool/Executor receipts describe entry observations, not separate external effects.
These are internal audit records, without a writable receipt API, replay, remote-state
verification or tool support in the process worker. Existing direct tool-call HTTP
requests and direct Agent filesystem operations are outside this tracing scope.
See [EPI-01](episode-records-epi01.md).

`GET /api/tool-observations/{action_id}?event_id=...` reads up to 32 stored file samples.
`POST /api/tool-observations/{action_id}` accepts only `event_id` and strict integer
`expected_count` (0–31) and samples an unknown receipt's server-fixed file intent.
It never accepts paths, replacement hashes, observations or completion status.
Checks run outside the write transaction; immutable binding and count fences guard
the append. Unsupported intents, wrong identities, non-unknown receipts and stale
counts return 409; extra/invalid fields return 422; disabled/stopped or unavailable
storage returns 503 (stopped admission only blocks POST). Successful responses are no-store.
Episode tool projections include `reconciliation`: fixed intent, count, newest three
samples and truncation status. A bad sample history does not discard the original
receipt/Episode. File matches do not prove execution success, causality or goal
completion, and do not unblock retries. See [REC-01](tool-reconciliation-rec01.md).

### Historical Agent Input Graph

Authenticated `GET /api/action-context/{task_id}/graph?event_id=...` projects the
ACT-04 invocation and saved world/memory input references. Requires the durable
request store and an existing recorded agent intent. A source-event mismatch is
409; absent intent is 404; unavailable/corrupt storage is 503; invalid or missing
parameters are 422. Successful responses are `private, no-store`.

The bounded graph has at most 13 A/I nodes and 12 `action_input` edges. References
contain scopes, revisions, memory IDs and content hashes, without claim tokens,
historical bodies or present-day memory substitution. Reading neither replays
actions nor updates goals. See [ACT-05](action-input-graph-act05.md) for UI and
deployment boundaries.

## Authentication

Optional Cloudflare Access mode requires all four `EVA_CF_ACCESS_TEAM_DOMAIN`,
`EVA_CF_ACCESS_AUD`, `EVA_CF_ACCESS_ALLOWED_EMAILS` and `EVA_PUBLIC_ORIGIN` settings.
In this mode every HTTP route and WebSocket handshake requires a verified Access
JWT or a valid local `X-API-Token`, including health, docs, metrics and static
assets. Browser writes and upgrades check Origin; HTTP responses disable caching.
Verified browser identities are adapted internally to the existing API boundary,
so the browser does not need the long-lived API token. Public Tunnel traffic also
passes Cloudflare Access and connector JWT validation. See
[Cloudflare deployment](cloudflare-deployment.md) for configuration and rollout status.

Without Cloudflare Access configured, the original authentication rules apply:

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
  "text": "Summarize the current EVA architecture",
  "mode": "normal"
}
```

`mode` is optional and accepts `normal` (default) or `deep`. The browser sends the
selected value with every request and the server copies it into the admitted task
before agent execution. `GET /api/chat/modes` reports the configured strategy and
cube order for each mode. A response receipt echoes `mode` and may include
`mode_info` such as `strategy: native` or `strategy: prompt`; these fields describe
the configured adapter path, not a claim that private reasoning tokens are exposed.
Changing the UI selection while a reply is running applies to the next request only.

The endpoint returns immediately:

```json
{
  "accepted": true,
  "task_id": "correlation-id",
  "event_id": "event-id",
  "request_deadline_sec": 300.0,
  "result_retention_sec": 60.0,
  "message": "event queued; listen on WS chat_reply or poll /api/chat/result/{task_id}"
}
```

Receive the result from the WebSocket `chat_reply` channel or poll:

`GET /api/chat/result/{task_id}`

A pending/running result returns HTTP 202. A terminal result returns HTTP 200
and remains repeatably readable for `EVA_RESULT_TTL_SEC` after completion.
Unknown IDs, expired retention and process restarts return HTTP 404 with
`error=result_unknown_or_expired`; this process cannot distinguish those causes.
Results include `reply`, `selected_agent`, `loop_id`, `duration_ms`, `ok`, `error`,
`review`, `memory_write_ids`, `terminal_state` and optional `execution_state`.
`completed=true` means a terminal receipt exists. It does not prove the underlying
work has stopped or a business goal succeeded. Worker timeout/cancellation may
produce `outcome_unknown`; queued expiry produces `expired` without execution.

All chat endpoints accept optional `remember: true` (a strict boolean) to retain
the exact user message in S3 as a `user_statement`. The response's
`memory_write_ids.s3`, when present, is the actual retention receipt. The default
is false. This does not disable ordinary transcript, event or episodic storage.

### Synchronous Compatibility

`POST /api/chat/sync`

This publishes the same event but blocks for `EVA_REQUEST_TIMEOUT_SEC`. It
returns HTTP 202 with `wait_timed_out=true` if no receipt is available in that
window. The request is not cancelled; the separate pending deadline defaults to
300 seconds. Successful sync delivery also preserves its receipt for polling.

### Server-Sent Events

`POST /api/chat/stream`

This runs the full cognition pipeline and returns `text/event-stream`. Final text
arrives as a `data:` frame, followed by an `event: result` JSON frame and
`data: [DONE]`. Read the `X-EVA-Task-ID` header for subsequent polling. The registry
is authoritative, so missed WebSocket broadcasts cannot leave SSE waiting forever.
Disconnecting does not cancel or resubmit work. SSE clients must distinguish named
receipt frames from ordinary text frames.
Final text is held until the structured response check completes, so early draft
tokens and tool progress are not sent. Rejected responses send an uncertainty
message, not the rejected draft. Policy denials and non-streaming agents also
produce a final text frame before `[DONE]`.

`review.status` is `not_assessed`, `contract_checked`, `blocked`, or
`execution_failed`. `review.fact_verified` remains false: receipt validation is
not proof of factual correctness. Plain-text agents remain compatible and report
`not_assessed` rather than a successful truth assessment.

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

Tool descriptions expose `requires_user_authorization`. `ingest_document` changes
long-term memory and is blocked in model-generated tool calls. To import a file,
explicitly submit its exact tool/arguments to authenticated `POST /api/tools/call`:

```json
{"tool": "ingest_document", "args": {"path": "docs/architecture.md"}}
```

The existing workspace and executor limits still apply. Importing a document
records its contents and source, not the truth of its statements.

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

World entities and relations include `provenance`; entities also include
`field_provenance` keyed by `name` and `properties.<key>`. S4 browse, search and
detail retain the same origins. Missing legacy origins are `unknown`; mixed
entities are unknown as a whole while individual fields retain their origins.
These labels do not establish factual truth. See [EVD-01](world-provenance-evd01.md)
for schema, migration and context projection compatibility.

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
