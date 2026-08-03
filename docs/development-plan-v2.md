# EVA Development Execution Plan v2

Status: canonical implementation plan after the architecture consolidation.

The objective is to turn the current single-node prototype into a dependable,
demonstrable personal cognitive runtime without replacing stable components all
at once. Each phase must leave the system runnable and backward compatible.

## Current Baseline

The repository already provides:

- FastAPI, dashboard, WebSocket, async/sync/stream chat paths
- event bus, continuous cognition loop, scheduler, result registry, snapshots
- planner, policy engine, agent registry/router/orchestrator, built-in agents
- structured and tiered memory, persona/self-model, lightweight world graph
- GitHub polling/webhook integration, health, diagnostics, metrics, audit APIs
- a stable composition root and an opt-in experimental MVSC bridge
- a replaceable agent worker protocol with a bounded thread backend

The baseline is single-process. Thread timeouts are cooperative, external
connectors are incomplete, and the world/attention model is not yet the full
eight-state model described by the research documents.

## Delivery Principles

1. Keep one stable event and cognition path.
2. Replace implementations behind contracts instead of rewriting the system.
3. Treat external events, model output, and tool arguments as untrusted input.
4. Preserve user focus: proactive behavior needs budgets, cooldowns, and audit.
5. Every phase ends with runnable code, migration coverage, and documentation.

## Roadmap

| Phase | Weeks | Outcome | Exit gate |
| --- | --- | --- | --- |
| 0. Architecture baseline | Complete | Grouped container, worker seam, registry lifecycle, canonical docs | Full suite and preflight pass |
| 1. Isolated agent workers | 1-2 | Long-lived process workers with hard lifecycle boundaries | Faulted worker is terminated and replaced |
| 2. Cognition and event contracts | 3-4 | Smaller cognition stages and versioned event/result schemas | Replay produces deterministic state transitions |
| 3. World state and attention | 5-6 | Eight-state world model, situation builder, calibrated interruption policy | Same event behaves correctly across user modes |
| 4. Hybrid memory and graph | 7-8 | Graph plus vector retrieval with provenance and compaction | Planner retrieves relevant cross-session context |
| 5. Connectors and proactive loop | 9-10 | Calendar/filesystem/GitHub normalization and reliable outbox | End-to-end proactive demo passes unattended |
| 6. Production readiness | 11-12 | Security, shared persistence path, observability, release packaging | Staging run survives restart and fault tests |

## Phase 0: Architecture Baseline

Status: implemented by the current refactor.

Delivered:

- `AppContainer` grouped into memory, persona, world, agents, runtime, and integrations
- compatibility properties for existing routes during migration
- `AgentWorkerBackend` protocol and `ThreadAgentWorkerBackend`
- configurable worker count and execution timeout
- thread-safe public `AgentRegistry.register/unregister` lifecycle
- runtime-configured chat agent bound to an explicit task kind
- canonical architecture, API, development, and planning documents

Follow-up cleanup ticket `BASE-01`:

- Migrate routes from flat container properties to grouped properties.
- Remove compatibility properties only after repository-wide search and tests
  show no callers.

## Phase 1: Isolated Agent Workers

Goal: ensure agent crashes, memory growth, and timeouts cannot destabilize EVA Core.

### Work Items

`WRK-01` Define serializable IPC contracts.

- Add versioned `WorkerRequest`, `WorkerProgress`, and `WorkerResponse` models.
- Carry task, agent name, trace ID, deadline, capability grants, and result metadata.
- Reject unknown schema versions and non-serializable payloads before dispatch.

Target: `runtime/worker_protocol.py`, `tests/test_worker_protocol.py`.

`WRK-02` Implement a long-lived process pool backend.

- Add `ProcessAgentWorkerBackend` behind the existing protocol.
- Use spawn-compatible startup on Windows.
- Add bounded input/output queues, heartbeat, graceful drain, and forced stop.
- Restart a worker after crash, protocol violation, timeout, or task-count limit.

Target: `runtime/process_worker.py`, `runtime/worker_main.py`.

`WRK-03` Enforce resource policy.

- Windows: assign each worker to a Job Object with process memory, active
  process, kill-on-close, and CPU-time limits.
- Linux/container: document cgroup/container equivalents.
- Distinguish queue timeout, execution timeout, worker crash, and policy denial.

Target: `runtime/windows_job.py`, `runtime/resource_policy.py`.

`WRK-04` Move privileged tools behind the worker boundary.

- Pass capability grants, not live executor objects, across IPC.
- Keep file roots, network allowlists, and code execution opt-in.
- Preserve executor audit and correlation IDs.

### Acceptance Criteria

- Killing a worker during a task returns a structured failure and starts a replacement.
- A memory-limit test cannot terminate or exhaust EVA Core.
- A timed-out task leaves no reusable contaminated worker state.
- Streaming progress survives normal worker execution and closes cleanly on failure.
- Thread backend remains available for tests and constrained development use.

## Phase 2: Cognition And Event Contracts

Goal: reduce the cognition loop from a broad coordinator into explicit testable stages.

### Work Items

`COG-01` Introduce stage interfaces:

```text
Perception -> WorldUpdate -> Attention -> Situation -> Decision
-> Plan -> Execute -> Integrate -> Memorize
```

- Extract each stage from `core/cognition_loop.py` without changing behavior.
- Keep `CognitionLoop` responsible only for control flow, cancellation, and metrics.

`EVT-01` Version the event schema.

- Define event category, schema version, source identity, priority, payload,
  context, trace, causation ID, correlation ID, and idempotency key.
- Add normalizers for user, time, GitHub, calendar, filesystem, and agent results.
- Publish dead-letter events for invalid or repeatedly failing inputs.

`EVT-02` Add deterministic replay.

- Persist normalized events before side effects.
- Track handler outcome and state revision.
- Replay into an isolated world-state instance without executing external actions.

`COG-02` Add backpressure and queue metrics.

- Bound queues and expose age, depth, throughput, failures, and saturation.
- Reserve priority lanes for user messages and deadlines.

### Acceptance Criteria

- Stage unit tests can run without FastAPI or a global settings object.
- Replaying a fixed fixture yields the same world-state revision and trace.
- Duplicate idempotency keys do not execute an agent twice.
- Queue saturation degrades background work before interactive work.

## Phase 3: World State And Attention

Goal: make decisions from a compressed situation rather than raw messages.

### Work Items

`WLD-01` Implement `WorldStateV2` with explicit sections:

- user, system, focus, tasks, time, environment, relations, and watch state
- confidence, source, observed timestamp, and expiry for inferred fields
- persistent snapshots plus derived values such as interruptibility and temporal pressure

`WLD-02` Centralize update rules.

- Event-driven, inference-driven, execution-driven, and maintenance-driven updates
- optimistic state revision and migration from the current snapshot shape
- no direct state mutation by agents or API routes

`SIT-01` Build a compact `Situation` contract.

- Primary focus, active task, urgency, interruptibility, relevant entities,
  memory references, candidate agents, and permitted actions
- A deterministic builder plus optional LLM enrichment behind a feature flag

`ATT-01` Calibrate attention and proactive decisions.

- Relevance, urgency, goal alignment, novelty, relation weight, and surprise
- User-mode modifiers for normal, deep work, meeting, rest, and unavailable
- Decisions: ignore, archive, watch, notify, act
- Cooldown, per-hour interruption budget, deduplication, and escalation policy

### Acceptance Criteria

- Deep-work mode suppresses ordinary notifications but not critical deadlines.
- Repeated equivalent events consume one interruption budget entry.
- Every proactive decision records its features, threshold, persona adjustment,
  and final reason.
- Planner input uses `Situation`, not raw database rows or the complete world state.

## Phase 4: Hybrid Memory And World Graph

Goal: retrieve compressed experience and relationships across sessions with provenance.

### Work Items

`MEM-01` Stabilize memory records.

- Profile, episodic, semantic, and policy records with provenance and revision.
- Store summaries/tags and protected raw references, not unrestricted chat dumps.
- Central memory filter using importance, novelty, relation strength, and future relevance.

`MEM-02` Productionize hybrid retrieval.

- Lexical, vector, recency, importance, and graph-neighborhood scoring.
- Provider interface for local embeddings and a disabled/no-op mode.
- Rebuildable vector index with database IDs as the source of truth.

`GRF-01` Consolidate relation storage.

- Typed node/edge schema, uniqueness rules, edge weights, provenance, and timestamps.
- Upsert rules for user, project, task, document, note, reminder, and meeting entities.
- Neighbor, path, project-context, and related-memory queries.

`MEM-03` Implement safe compaction.

- Daily episodic summaries, semantic concept merge, retention rules, tombstones,
  and dry-run reports.
- Never delete policy, profile, document, or high-importance records without an
  explicit retention rule.

### Acceptance Criteria

- A task and document from separate sessions are retrieved through their project relation.
- Every semantic answer can report memory IDs and source provenance.
- Index deletion/rebuild does not lose canonical memory records.
- Compaction is idempotent and covered by backup/restore tests.

## Phase 5: Connectors And Proactive Loop

Goal: create reliable external perception and a low-noise proactive demonstration.

### Work Items

`CON-01` Standard connector contract.

- `poll`, `normalize`, `checkpoint`, `health`, and `close`
- Persistent cursors, deduplication, retry/backoff, rate-limit handling, and secrets redaction

`CON-02` Harden GitHub.

- Verify webhook signatures, delivery IDs, event allowlist, and repository scope.
- Normalize pull request, issue, workflow, and push events.
- Add review-requested, workflow-failed, issue-assigned, and default-branch rules.

`CON-03` Add calendar and filesystem connectors.

- Calendar provider interface with a mock and one real provider.
- Filesystem watch restricted to configured roots with debounce and ignore patterns.
- No connector directly invokes an agent.

`OUT-01` Add a durable proactive outbox.

- States: queued, suppressed, delivered, acknowledged, expired, failed
- Delivery retries, deduplication key, channel policy, and audit trail
- WebSocket/dashboard delivery first; external messaging remains opt-in

### Acceptance Criteria

- Restarting EVA does not duplicate already checkpointed GitHub or calendar events.
- Invalid webhook signatures are rejected and audited without storing secrets.
- Demo: a related document update plus an approaching meeting yields one concise
  notification while ordinary updates remain in watch/archive.

## Phase 6: Production Readiness

Goal: make deployment and operations predictable before adding more agents.

### Work Items

`SEC-01` Security baseline.

- Secret manager/environment integration, token rotation guidance, startup secret scan
- scoped API tokens or authenticated user sessions, CSRF policy for UI writes
- endpoint authorization for debug, executor, persona, import/export, and recovery APIs

`DAT-01` Shared persistence path.

- Complete PostgreSQL adapter parity and migrations.
- External durable event broker and result store before enabling multiple API processes.
- Scheduler leader election or a dedicated scheduler service.

`OBS-01` Operational telemetry.

- Structured logs with correlation IDs and redaction
- OpenTelemetry traces and stable Prometheus metric names
- SLOs for chat latency, event age, worker failure, connector lag, and outbox delivery

`REL-01` Release packaging.

- Versioned database and snapshot migrations
- Docker health/startup probes, immutable image, non-root runtime
- backup/restore drill, upgrade/rollback script, demo seed data, release checklist

`EXP-01` Resolve the MVSC experiment.

- Measure it against stable-loop correctness, latency, noise, and retrieval quality.
- Promote only proven contracts into stable modules; otherwise keep or remove the experiment.
- Never operate two authoritative world, memory, or policy systems.

### Acceptance Criteria

- Production refuses to start without required authentication and secure settings.
- Backup restore recovers state, graph, memory, and connector checkpoints.
- A 24-hour staging run has no unbounded queue, thread, worker, or memory growth.
- Upgrade and rollback complete without manual database edits.

## Cross-Cutting Test Strategy

Every phase adds tests at four levels:

| Level | Required coverage |
| --- | --- |
| Contract | Event, IPC, situation, result, memory, and connector schemas |
| Component | Stage behavior with injected fakes and deterministic clocks |
| Integration | Event to result, restart recovery, connector to outbox |
| Fault | Timeout, process crash, malformed input, DB lock, network failure, duplicate delivery |

The required quality gate is:

```powershell
python -m ruff check .
python -m pytest -q
python scripts/preflight.py
```

## Definition Of Done

A work item is complete only when:

1. Behavior is behind an explicit interface or schema.
2. Configuration is injected and represented in `.env.example`.
3. Success, denial, timeout, cancellation, and recovery paths are tested.
4. Logs and metrics do not expose secrets or raw sensitive payloads.
5. State migrations and rollback implications are documented.
6. Canonical architecture/API/runbook documents reflect the behavior.
7. The full quality gate passes on Windows and the container target.

## Immediate Backlog

Execute in this order:

1. `WRK-01`: worker IPC models and serialization tests.
2. `WRK-02`: spawn-safe process backend with heartbeat and replacement.
3. `WRK-03`: Windows Job Object resource policy integration.
4. `COG-01`: extract result integration and memory writeback stages first.
5. `EVT-01`: version event/result schemas before adding connectors.
6. `WLD-01`: introduce WorldStateV2 behind a snapshot migration adapter.

Do not begin distributed deployment, more agent types, or another memory stack
before these six items are complete.
