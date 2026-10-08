# EVA Cognitive Runtime

EVA is a continuously running, event-driven personal cognitive assistant host.
It combines world state, structured memory, persona constraints, planning,
agent execution, proactive scheduling, and observable interfaces in one stable
single-node runtime.

## What Works Today

- FastAPI dashboard, REST, WebSocket, SSE, and async/sync chat flows
- [Obsidian memory graph](docs/obsidian-memory-graph.md): particle graph UI, provenance links, conflict-aware Markdown mirror and read-only local viewer
- event bus and continuous cognition loop with traceable result integration
- shared v1 event contracts, explicit legacy upgrades, source occurrence IDs, and isolated admission snapshots
- shared EventProcessor and RuntimeController with ordered shutdown, retryable cleanup, and live runtime observations
- planner, policy engine, agent registry/router/orchestrator, runtime agent registration
- chat, search, coding analysis, and documentation agents
- mock and configured LLM providers with guarded file/memory/network/code tools
- SQLite/JSON persistence, episodic and tiered memory, retrieval, compaction, snapshots
- provenance-aware memory, explicit S3 retention, and structured response receipt checks
- persona, relationship constraints, profile, self-model, and stability controls
- lightweight world graph, context building, scheduler, cooldown, and proactive reminders
- GitHub poller/webhook integration, health, diagnostics, metrics, audit, and recovery APIs
- optional MVSC experiment adapters behind a disabled-by-default flag; HTTP still uses the legacy consumer
- optional [Minimal Brain v0.1](docs/minimal-brain-v01.md) with independent state-node timing, bounded attention/workspace, and the existing event processor

Current limits are explicit: the stable runtime has one Core process; the default
agent worker uses threads. An opt-in [process backend](docs/process-workers.md)
can terminate and replace text-agent workers, but has no tools or OS resource sandbox;
the world graph is lightweight; calendar/filesystem connectors and a fully
durable proactive outbox remain planned.

Actual legacy/Minimal/MVSC wiring and the fixed offline HTTP benchmark are documented
in [BASE-02](docs/runtime-baseline-base02.md). The UI reads the selected consumer from
`/api/runtime`; requesting MVSC does not activate its experimental loop as that consumer.

## Quick Start

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

- Memory graph: `http://127.0.0.1:8000/`
- Chat: `http://127.0.0.1:8000/chat`
- API documentation: `http://127.0.0.1:8000/docs`
- Liveness: `http://127.0.0.1:8000/health/live`

Queue a message:

```powershell
$body = @{ text = "Show the current EVA state" } | ConvertTo-Json
$queued = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat `
  -ContentType "application/json" -Body $body
Invoke-RestMethod "http://127.0.0.1:8000/api/chat/result/$($queued.task_id)"
```

For blocking local scripts, use `POST /api/chat/sync`. Applications should use
`POST /api/chat` plus WebSocket `chat_reply`, SSE, or polling.

## Architecture

```text
Interface / Connectors
        -> EventBus
        -> Selected consumer: FIFO loop or Minimal Brain
        -> EventProcessor
        -> World + Memory + Persona context
        -> Planner + Policy
        -> AgentOS
        -> AgentWorkerBackend
        -> Agent + guarded tools
        -> Result Event
        -> Memory / World / Trace / Client
```

The application container is grouped into `memory`, `persona`, `world`,
`agents`, `runtime`, and `integrations` subsystems. `app/bootstrap.py` composes
one `RuntimeController`; it owns consumer lifecycle and ordered cleanup.
Minimal Brain adds recurring state/attention/goal updates alongside slow event
processing. See [Runtime Refactor v0.2](docs/runtime-refactor-v02.md) and inspect
the actual consumer, node timings and shutdown state at `GET /api/runtime`.

```text
app/          API edge, configuration, composition, lifecycle
event/        Stable event contracts and bus
core/         Cognition, planning, policy, context, proactive decisions
agent_os/     Agent registry, routing, orchestration
agents/       Task handlers
memory/       Persistence, retrieval, tiers, governance
persona/      Persona, relationship, profile, self-model
world/        Current state, graph, snapshots
runtime/      Workers, scheduler, auth, health, diagnostics, messaging
connectors/   External event sources
packages/     Optional Minimal Brain and MVSC through `app/experimental.py`
ui/web/       Dashboard
tests/        Behavior, integration, recovery, and architecture checks
```

## Documentation

Start at [docs/README.md](docs/README.md).

| Document | Purpose |
| --- | --- |
| [Current Architecture](docs/architecture.md) | Runtime topology, boundaries, status, and risks |
| [Development Guide](DEVELOPMENT.md) | Setup, extension workflows, and quality gates |
| [API Reference](docs/api-reference.md) | Chat contracts and endpoint families |
| [Development Plan v2](docs/development-plan-v2.md) | Concrete 12-week prioritized roadmap |
| [Production Runbook](docs/production-runbook.md) | Secure single-node operation and recovery |

## Configuration

Application settings use the `EVA_` prefix. See `.env.example` and
`app/config.py` for the complete set.

Important defaults:

```text
EVA_COGNITION_WORKER_COUNT=1
EVA_AGENT_WORKER_COUNT=4
EVA_AGENT_EXECUTION_TIMEOUT_SEC=120
EVA_ENABLE_CODE_TOOL=false
EVA_ENABLE_NETWORK_TOOLS=false
EVA_ENABLE_MVSC_PIPELINE=false
EVA_ENABLE_MINIMAL_BRAIN=false
```

Set `EVA_API_TOKEN` in production and send it as `X-API-Token`. Never commit
provider keys, GitHub tokens, webhook secrets, or user-specific absolute paths.
Code and network tools are privileged opt-in capabilities.

## Verification

```powershell
python -m ruff check .
python -m pytest -q
python scripts/preflight.py
```

Do not run multiple Uvicorn workers or multiple EVA replicas with the current
in-memory bus/result registry and SQLite topology. Process agents do not change
these Core limits. OS sandboxing and distributed-runtime prerequisites remain planned.

## Docker

```powershell
$env:EVA_API_TOKEN = "replace-with-a-long-random-token"
docker compose up --build -d
```

The Compose topology intentionally runs one EVA API process with persistent
data/log volumes and container-level resource limits.

## License

See `LICENSE`.
