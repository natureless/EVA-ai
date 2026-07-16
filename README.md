# EVA v0.1 → EVA-VM v1

EVA is an event-driven cognitive agent host designed to be:

- continuously running
- interactive
- stateful
- observable

## Architecture

EVA is built on two foundational documents:

- **[EVA-VM Architecture v1](docs/eva-vm-architecture.md)** — the system blueprint:
  host boundary → VM body → container organs → core cognition → memory continuity → policy constitution → action executors
- **[Information Dynamics Consciousness Model](docs/consciousness_model.md)** —
  the theoretical framework: L0-L5 consciousness hierarchy, self-referential information loop, experience intensity formula

Core insight: **experience is a self-referential information system modeling its own state changes.**

## Features

### Core Capabilities

- **FastAPI backend** - High-performance REST API with automatic documentation
- **Event bus** - Asynchronous message passing between components
- **Cognition loop** - Continuous event processing and agent orchestration
- **Agent system** - Extensible agent registry with router and orchestrator
- **SQLite persistence** - Events, memory, and trace logs stored persistently
- **Snapshot/restore** - Full system state serialization and recovery
- **Scheduler** - APScheduler-based periodic tasks and maintenance
- **Proactive engine** - Stagnation detection and automatic reminders
- **Observability** - Trace, memory, and event logging with querying

### Built-in Agents

- **chat_agent** - General conversational task handler
- **search_agent** - Full-text search across local files
- **coding_agent** - File summarization and code analysis
- **docs_agent** - Documentation search and retrieval

## Project Structure

```text
app/              FastAPI bootstrapping, config, API routes
core/             Cognition loop, planner, proactive engine, context builder
event/            Event schema, event bus implementation
agents/           Base agent, specialized agent implementations
agent_os/         Agent registry, router, orchestrator
memory/           SQLite store, memory API, memory governor, profiles
persona/          Persona service, self-model, personality management
world/            World model, snapshot store
runtime/          Health checks, scheduler, result registry, logging
ui/web/           Dashboard templates and static assets
tests/            Comprehensive test suite
data/             Runtime data (SQLite, snapshots, profiles)
logs/             Application logs
```

## Requirements

- Python 3.11+
- See `requirements.txt` for full dependency list

## Installation

### 1) Create virtual environment

**Windows:**
```bash
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux:**
```bash
python -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Run the application

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Access the application:
- **Dashboard:** http://127.0.0.1:8000/
- **API Docs:** http://127.0.0.1:8000/docs
- **Health Check:** http://127.0.0.1:8000/health/live

## Configuration

Configuration is controlled via environment variables with `EVA_` prefix:

### Core Settings
- `EVA_ENV` - Environment: `dev` or `production` (default: `dev`)
- `EVA_HOST` - Server bind address (default: `0.0.0.0`)
- `EVA_PORT` - Server port (default: `8000`)
- `EVA_LOG_LEVEL` - Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (default: `INFO`)

### Path Configuration
- `EVA_DATA_DIR` - Data directory (default: `data`)
- `EVA_LOG_DIR` - Log directory (default: `logs`)
- `EVA_DB_PATH` - Database path (default: `data/eva.db`)
- `EVA_SNAPSHOT_DIR` - Snapshots directory (default: `data/snapshots`)

### Timing Configuration (seconds)
- `EVA_TICK_INTERVAL_SEC` - Cognition loop tick interval (default: `0.5`)
- `EVA_QUEUE_POLL_TIMEOUT_SEC` - Event queue poll timeout (default: `0.5`)
- `EVA_REQUEST_TIMEOUT_SEC` - API request timeout (default: `8.0`)
- `EVA_RESULT_TTL_SEC` - Result registry TTL (default: `60.0`)
- `EVA_SCHEDULER_TICK_INTERVAL_SEC` - Scheduler interval (default: `10`)
- `EVA_SCHEDULER_MAINTENANCE_INTERVAL_SEC` - Maintenance interval (default: `60`)
- `EVA_SCHEDULER_SNAPSHOT_INTERVAL_SEC` - Snapshot interval (default: `120`)
- `EVA_STAGNATION_THRESHOLD_SEC` - Inactivity threshold (default: `86400`)
- `EVA_REMINDER_COOLDOWN_SEC` - Reminder cooldown (default: `43200`)

### Feature Flags
- `EVA_ENABLE_V02_PIPELINE` - Enable experimental v0.2 pipeline (default: `false`)

## Docker Deployment

### Build and run with Docker Compose

```bash
docker-compose up --build
```

Application will be available at http://localhost:8000

### Build image manually

```bash
docker build -t eva:latest .
docker run -p 8000:8000 -v $(pwd)/data:/app/data -v $(pwd)/logs:/app/logs eva:latest
```

### Docker Compose Configuration

The `docker-compose.yml` includes:
- Volume mounts for data persistence
- Environment variable configuration
- Health checks
- Auto-restart policy

## API Endpoints

### Chat
`POST /api/chat` - Send a message and get a response

**Request:**
```json
{
  "text": "Tell me about the current system state"
}
```

**Response:**
```json
{
  "accepted": true,
  "completed": true,
  "event_id": "...",
  "correlation_id": "...",
  "reply": "...",
  "selected_agent": "chat_agent",
  "loop_id": "...",
  "duration_ms": 123
}
```

### State & Observability
- `GET /api/state` - Current system state
- `GET /api/memory/recent` - Recent memory entries
- `GET /api/debug/trace` - Execution trace log
- `GET /api/events/recent` - Recent events
- `GET /api/agents` - Registered agents list
- `GET /health/live` - Liveness probe
- `GET /health/ready` - Readiness probe

### Agent Commands
Commands are prefixed with `/` and route to specialized agents:
- `/search <query>` → search_agent (scans local files)
- `/code <path>` → coding_agent (file summary and analysis)

### Snapshot Management
- `GET /api/debug/snapshot` - Export current snapshot
- `POST /api/debug/snapshot/save` - Force save snapshot
- `POST /api/debug/snapshot/restore` - Restore from snapshot

### Proactive & Maintenance
- `GET /api/proactive/state` - Proactive engine state
- `POST /api/debug/maintenance/trigger` - Trigger maintenance
- `GET /api/scheduler/jobs` - Scheduled jobs status

## Testing

Run the test suite:

```bash
pytest -v          # Verbose output
pytest -q          # Quiet output
pytest --cov       # With coverage report
pytest -k "test_chat"  # Run specific tests
```

Test coverage:
- Chat flow integration tests
- Health check endpoints
- Memory governor functions
- Persona management
- Proactive engine triggers
- Snapshot save/restore

## Runtime Flow

### User Message Processing
```
HTTP /api/chat
→ Event bus (publish)
→ Cognition loop (consume)
→ Planner (decide)
→ Agent router (select)
→ Orchestrator (execute)
→ Memory/trace/state update
→ Result registry (return)
```

### Periodic Maintenance
```
Scheduler (tick)
→ System maintenance
→ Memory governor maintenance
→ Snapshot save
→ Stagnation check
```

### Proactive Reminders
```
Proactive engine (evaluate)
→ Detect stagnation/idle
→ Generate reminder event
→ Chat agent execution
→ Update world state
```

## Logging

Logs are written to `logs/eva.log` and console with configurable level.

Key logger names:
- `eva.bootstrap` - System startup/shutdown
- `eva.cognition_loop` - Cognition loop events
- `eva.search_agent` - File search operations
- `eva.scheduler` - Scheduler operations

## v0.1 Scope & Boundaries

### Included
- Single-node continuous operation
- Event-driven architecture
- Agent-based task execution
- Local file search and code analysis
- State persistence via SQLite
- Snapshot-based recovery

### Not Included (v0.2+)
- Multi-device synchronization
- Graph database backend
- Multi-agent parallelism
- Distributed deployment
- Advanced reasoning engines

## Development

### Code Quality Tools

Development tools are included in `requirements.txt`:
- **black** - Code formatting
- **ruff** - Linting
- **mypy** - Type checking
- **pytest-cov** - Coverage reporting

Format code:
```bash
black app agents core event memory persona runtime tests
```

Lint code:
```bash
ruff check .
```

Type check:
```bash
mypy app core agents
```

### Performance Optimization

Key performance considerations:
- Event queue polling with configurable timeout
- Result registry cleanup with TTL
- Search agent file size and count limits
- SQLite query optimization with indices
- Snapshot interval tuning

## Troubleshooting

### Database Issues
If you encounter SQLite errors, check:
- `data/eva.db` file exists and is readable
- No other processes have the database open
- Disk space availability

### Memory Usage
Monitor memory with:
```bash
GET /api/state  # Check pending_events and pending_results
GET /api/memory/recent  # Check stored memories
```

### Performance Degradation
Check logs for:
- Long agent execution times
- Large numbers of pending events
- Memory governor evictions

## Contributing

Follow the code quality standards:
1. Use type hints on all functions
2. Add docstrings to modules and classes
3. Keep functions focused and testable
4. Write tests for new features
5. Format with black, lint with ruff

## License

See LICENSE file for details.

