# EVA Operations Runbook

Deployment, startup, monitoring, recovery, and troubleshooting procedures.

---

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
source .venv/bin/activate        # macOS/Linux
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` for the dashboard, `http://localhost:8000/docs` for Swagger.

---

## Deployment

### Docker (single container)

```bash
docker build -t eva:latest .
docker run -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/config:/etc/eva:ro \
  -e EVA_ENV=production \
  eva:latest
```

### Docker Compose (multi-service)

```bash
docker-compose up --build
```

Services: `eva-core` (8000), `dashboard` (3000), `scheduler`, `memory-db`, `audit-agent`.

### Production (EVA-VM)

For the full VM-based deployment: [EVA-VM Architecture](eva-vm-architecture.md)

```
Host Linux
  → KVM + libvirt (eva-vm.xml)
    → EVA-VM (Ubuntu Server)
      → systemd → rootless Podman/Docker
        → eva-core + dashboard + scheduler + memory-db + audit-agent
```

---

## Startup Sequence

```
1. bootstrap_system()
   ├── ensure_dirs()                    # data/, logs/, templates/, static/, snapshots/
   ├── configure_logging()              # rotating file handler (2MB x 3)
   ├── SQLiteStore.init_db()           # CREATE TABLE IF NOT EXISTS (15 tables)
   ├── TieredMemoryManager             # S1(session) S2(working) S3(longterm) S4(world) S5(events)
   ├── ProfileStore / PersonaStore / SelfModelStore  # load JSON from disk
   ├── SnapshotStore.load_latest()     # restore world model from snapshot
   │   └── WorldModelGraph.from_dict() + load_from_store(S4)
   ├── EventBus / Planner
   ├── AgentRegistry (chat/search/coding/docs)
   ├── ResultRegistry
   ├── ProactiveEngine
   ├── ContextBuilder(persona, tiered_memory, world_model)
   ├── PolicyEngine(config/policy.yaml)
   ├── Executors(file/code/browser/api/comms)
   ├── CognitionLoop.start()           # daemon thread
   ├── RuntimeScheduler.start()        # APScheduler background
   └── SystemDiagnostic.run_full()     # health=100 → boot log
```

### Expected Bootstrap Log

```
INFO eva.bootstrap - bootstrap start with env=dev port=8000
INFO eva.bootstrap - database initialized at data/eva.db
INFO eva.bootstrap - profile, persona, and self-model loaded
INFO eva.bootstrap - snapshot loaded from data/snapshots/latest.json
INFO eva.bootstrap - event bus initialized
INFO eva.bootstrap - planner initialized
INFO eva.bootstrap - agent registry initialized with 4 agents
INFO eva.bootstrap - policy engine initialized
INFO eva.bootstrap - executor framework initialized (5 executors)
INFO eva.bootstrap - cognition loop started
INFO eva.bootstrap - scheduler started with tick_interval=10 sec
INFO eva.bootstrap - bootstrap complete - system ready
INFO eva.bootstrap - boot diagnostic: score=100 overall=healthy
```

---

## Monitoring

### Dashboard (`/`)

9-panel live telemetry with WebSocket push. Displays policy state (●dormant/●commanded/●quarantined), memory tier stats, world model entities, audit timeline, health score.

### Health Endpoints

```bash
curl http://localhost:8000/health/live        # {"status": "alive"}
curl http://localhost:8000/health/ready       # per-component boolean
curl http://localhost:8000/health/diagnostic  # full scored diagnostic
curl http://localhost:8000/health/ws          # WS connection stats
```

### Log Files

- `logs/eva.log` — rotating file handler (2MB x 3 backups)
- Console output — all levels
- Component loggers: `eva.bootstrap`, `eva.cognition_loop`, `eva.executor`, `eva.memory_api`, `eva.llm`, `eva.websocket`

### Key Metrics

| Metric | Endpoint | Normal Range |
|--------|----------|-------------|
| `pending_events` | `/api/state` | 0-5 |
| `pending_results` | `/api/state` | 0-2 |
| Policy state | `/api/policy/state` | dormant |
| Health score | `/health/diagnostic` | 90-100 |
| S1 entries | `/api/memory/tiers` | 0-50 (transient) |
| S4 entities | `/api/memory/tiers` | accumulating |

---

## Recovery Procedures

### Quarantine Recovery

If the system enters `Quarantined` state (indicated by red dot on dashboard):

```bash
curl -X POST http://localhost:8000/health/recover \
  -H "Content-Type: application/json" \
  -d '{"action": "reset_policy"}'
```

Or via dashboard: send `/policy transition manual_intervention`.

### Stale Result Registry

If `pending_results` is growing (blocked chat requests):

```bash
curl -X POST http://localhost:8000/health/recover \
  -d '{"action": "clear_registry"}'
```

### Corrupt Snapshot

If snapshot won't load on boot:

```bash
# Option 1: Delete and regenerate
rm data/snapshots/latest.json
curl -X POST http://localhost:8000/health/recover \
  -d '{"action": "rebuild_snapshot"}'
```

### DB Integrity Issues

```bash
# Safe re-init (CREATE IF NOT EXISTS)
curl -X POST http://localhost:8000/health/recover \
  -d '{"action": "rebuild_db"}'
```

### Full System Reset

```bash
# Stop server, then:
rm -rf data/eva.db data/snapshots/ data/profile.json data/persona.json data/self_model.json
# Restart — all tables and files re-created with defaults
```

---

## Troubleshooting

### Won't Start

1. Check `logs/eva.log` for traceback
2. Verify `data/` directory is writable
3. Check port 8000 isn't in use: `netstat -an | findstr 8000`
4. Run diagnostic standalone: `python -c "from runtime.diagnostics import SystemDiagnostic; ..."`

### Database Locked

SQLite single-writer — ensure only one instance running. Kill stale process.

### Chat Returns 202 (Timeout)

Event was queued but cognition loop didn't process within `EVA_REQUEST_TIMEOUT_SEC` (default 8s). Check:
- `pending_events` in `/api/state` — high value means loop is backed up
- Cognition loop errors in `logs/eva.log`

### Memory Usage High

- S2 working memory: clean via `POST /api/debug/maintenance/trigger`
- S3 long-term: auto-archived when approaching max (10000 entries)
- Result registry: auto-cleaned every cycle (TTL=60s)

---

## Configuration

See `.env.example` for all tunables. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `EVA_ENV` | `dev` | Environment (dev/production) |
| `EVA_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `EVA_DB_PATH` | `data/eva.db` | SQLite database path |
| `EVA_STAGNATION_THRESHOLD_SEC` | `86400` | Idle time before reminder (24h) |
| `EVA_REQUEST_TIMEOUT_SEC` | `8.0` | Chat request timeout |
| `ANTHROPIC_API_KEY` | (none) | Claude API key (mock mode if unset) |
| `OPENAI_API_KEY` | (none) | OpenAI API key (mock mode if unset) |
| `EVA_LLM_PROVIDER` | (auto) | `claude` or `openai` |
| `EVA_LLM_MODEL` | (auto) | Model ID override |

---

## Backup & Restore

### What to Backup

- `data/eva.db` — all memory tiers + events + traces + audit
- `data/snapshots/latest.json` — world model snapshot
- `data/profile.json`, `data/persona.json`, `data/self_model.json` — persona state
- `config/` — persona, policy, executors, storage YAMLs
- `logs/` — application logs

### Backup

```bash
cp data/eva.db data/eva.db.bak
cp data/snapshots/latest.json data/snapshots/latest.json.bak
```

For the full VM deployment, use `infra/host/backup-restore.sh`.

### Restore

```bash
# Stop server, then:
cp data/eva.db.bak data/eva.db
cp data/snapshots/latest.json.bak data/snapshots/latest.json
# Start server — snapshot auto-loaded on bootstrap
```

---

## Performance Benchmarks

```bash
# Internal layer benchmarks (no server needed)
python scripts/benchmark.py --in-process --report

# HTTP API benchmarks (server must be running)
python scripts/benchmark.py --total 100 --concurrency 10 --report

# CI JSON output
python scripts/benchmark.py --in-process --json
```

Expected (in-process, warm cache):

| Layer | Per-op |
|-------|--------|
| importance_scorer | ~1µs |
| policy_evaluate | ~2µs |
| world_model_upsert | ~3µs |
| entity_extraction | ~13µs |
| memory_ingest (S1+S2) | ~10ms |
| diagnostics (full) | ~50ms |
