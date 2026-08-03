# EVA Development Guide

This guide applies to the stable runtime. Read `docs/architecture.md` before
changing lifecycle, event flow, cognition, memory, or execution boundaries.

## Setup

Requirements: Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS/Linux activation is `source .venv/bin/activate`.

Start the development server:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Useful entry points:

- Dashboard: `http://127.0.0.1:8000/`
- Settings: `http://127.0.0.1:8000/settings`
- OpenAPI: `http://127.0.0.1:8000/docs`
- Liveness: `http://127.0.0.1:8000/health/live`

Use one Uvicorn worker. The current event bus, result registry, scheduler, and
some state are process-local.

## Quality Gate

Run before a commit or pull request:

```powershell
python -m ruff check .
python -m pytest -q
python scripts/preflight.py
```

Focused commands:

```powershell
python -m pytest -q tests/test_chat_flow.py
python -m pytest -q tests/test_architecture.py
python -m pytest -q -k "worker or registry"
python -m black --check app core event agent_os agents memory persona runtime world tests
python -m mypy app core agents
```

Ruff and the full test suite are mandatory. Black and mypy should be used on
touched surfaces while legacy typing/formatting debt is reduced incrementally.

## Repository Boundaries

```text
app/          FastAPI edge, lifecycle, configuration, composition
event/        Event schema and bus
core/         Cognition, context, planning, policy, proactive decisions
agent_os/     Agent registry, routing, orchestration
agents/       Task handlers with injected dependencies
memory/       Persistence, retrieval, tiers, compaction, governance
persona/      Persona, profile, relationship, self-model
world/        Current world state, graph, snapshots
runtime/      Workers, scheduling, auth, health, diagnostics, messaging
connectors/   External event sources
packages/     Experimental MVSC modules behind one bridge
ui/web/       Dashboard templates and static assets
tests/        Contract, component, integration, and architecture tests
```

The main dependency rules are:

1. Domain modules do not import FastAPI, `app.config`, or `AppContainer`.
2. Dependencies are constructed in `app/composition.py` and assembled in
   `app/bootstrap.py`.
3. API routes publish events or call application services; they do not build
   domain components.
4. Agents return `AgentResult`; they do not mutate memory or world state directly.
5. External sources normalize input into stable events and never call agents.
6. `packages/*` is reachable from stable code only through `app/experimental.py`.

Architecture tests enforce these rules. Add a test when introducing a new rule.

## AppContainer Usage

New code should use the grouped subsystem model:

```python
container.memory.api
container.persona.service
container.world.model
container.agents.registry
container.runtime.event_bus
container.integrations.github_poller
```

Flat properties such as `container.memory_api` are temporary compatibility
aliases. Do not add new aliases.

## Adding An Agent

Create an agent that depends only on explicit constructor arguments:

```python
from agents.base_agent import AgentResult, AgentTask, BaseAgent


class MyAgent(BaseAgent):
    name = "my_agent"
    description = "Handle my_kind tasks"

    def __init__(self, service: object) -> None:
        self.service = service

    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "my_kind"

    def run(self, task: AgentTask) -> AgentResult:
        content = str(task.payload.get("text", ""))
        return AgentResult(
            ok=True,
            agent=self.name,
            content=content,
            summary=content[:120],
        )
```

Then:

1. Construct and register it in `app/composition.py`.
2. Add planner intent mapping only if the task kind cannot already be selected.
3. Add agent, routing, and end-to-end chat tests.
4. Add capabilities through guarded tools/executors, not direct file/network access.

Runtime registration through `/api/agents/register` creates an LLM-backed
configured agent for an allowed task kind. It is not a plugin or arbitrary code
loading mechanism.

## Adding A Connector

1. Put provider code under `connectors/`.
2. Inject credentials and configuration from the composition root.
3. Normalize provider payloads into `event.event_schema.Event`.
4. Persist cursor/delivery IDs when the provider supports replay.
5. Publish to `EventBus`; do not invoke planner or agents directly.
6. Add signature validation, deduplication, backoff, secret redaction, and health tests.

## Adding An API Route

1. Add a focused router under `app/api_routes/`.
2. Read the grouped container from `request.app.state.container`.
3. Use Pydantic request/response models for mutable operations.
4. Apply authentication/policy checks before privileged work.
5. Include the router in `app/api.py` and verify `/openapi.json`.
6. Add success, validation, authentication, and failure tests.

## Changing Events Or State

Event, result, snapshot, and memory shapes are persistence contracts.

- Add a version or migration instead of silently changing stored fields.
- Preserve correlation and causation IDs through derived events.
- Make handlers idempotent when external sources may redeliver.
- Keep inference values separate from observed facts and record confidence/source.
- Test recovery from an older snapshot/database fixture.

## Agent Worker Development

`runtime/agent_worker.py` is the execution seam. The current thread backend is
bounded but not an isolation boundary. A future process/container backend must
preserve:

- sync and streaming execution contracts
- structured timeout/cancellation/failure results
- correlation and audit metadata
- worker health and statistics
- deterministic shutdown without orphan processes

Never pass live database connections, locks, FastAPI objects, or executors over
worker IPC.

## Configuration And Secrets

All application settings use the `EVA_` environment prefix and are defined in
`app/config.py`. Keep `.env.example` synchronized.

- Never commit API tokens, webhook secrets, cookies, or user-specific absolute paths.
- Production requires `EVA_API_TOKEN` and should fail closed for privileged APIs.
- Code and network tools remain disabled unless explicitly enabled.
- Rotate a secret immediately if it appears in logs, chat, a commit, or test output.

## Documentation

Use `docs/README.md` as the index. Current behavior belongs in canonical docs;
future behavior belongs in `docs/development-plan-v2.md`; MVSC-specific behavior
belongs in experimental docs. OpenAPI is the endpoint schema authority.

## Common Failures

- Startup failure: run `python scripts/preflight.py` and inspect `logs/eva.log`.
- SQLite lock: stop duplicate EVA processes; do not delete lock or database files blindly.
- Chat accepted but no reply: poll `/api/chat/result/{task_id}`, inspect loop health,
  worker statistics in `/api/config`, and recent trace/events.
- LLM unavailable: verify provider-specific environment variables; mock mode should
  still support deterministic local tests.
- Connector silent: inspect connector health, repository/provider scope, checkpoint,
  rate-limit state, and normalized event log.
