# EVA Development Guide

## Development Environment Setup

### 1. Clone and Setup
```bash
git clone <repository-url>
cd EVA-ai
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Code Quality Tools

All development tools are installed with `requirements.txt`.

#### Formatting
```bash
black app agents core event memory persona runtime tests ui
```

#### Linting
```bash
ruff check .
```

#### Type Checking
```bash
mypy app core agents
```

#### Testing with Coverage
```bash
pytest --cov=app --cov=core --cov=agents --cov-report=html
```

### 3. Running Locally

Development mode with auto-reload:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Production mode:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## Code Standards

### Style Guide
- Use Black for formatting (line length: 100 characters)
- Follow PEP 8 with Ruff for linting
- Use type hints on all functions and methods
- Add docstrings to modules, classes, and public methods

### Type Hints
```python
from typing import Optional, List, Dict, Any

def process_event(event: Event, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    """Process an event with timeout.
    
    Args:
        event: The event to process
        timeout: Timeout in seconds
        
    Returns:
        Result dictionary or None if timeout
    """
    ...
```

### Documentation
- Module-level docstrings explaining purpose
- Class docstrings with attributes
- Method docstrings with Args, Returns, and Raises
- Inline comments only for non-obvious logic

```python
class EventBus:
    """Thread-safe event bus for publishing and consuming events.
    
    Manages a queue of events with configurable timeout behavior.
    """
    
    def publish(self, event: Event) -> None:
        """Publish an event to the bus.
        
        Args:
            event: Event to publish
            
        Raises:
            ValueError: If event is None
        """
        if event is None:
            raise ValueError("Cannot publish None event")
        self._queue.put(event)
```

### Error Handling
- Use specific exception types
- Log errors with context
- Return error results for recoverable failures
- Raise exceptions for unrecoverable failures

```python
try:
    result = process_data(data)
except ValidationError as e:
    logger.error("data validation failed: %s", e)
    return AgentResult(ok=False, content=str(e), ...)
except Exception as e:
    logger.exception("unexpected error: %s", e)
    raise
```

### Logging
- Use module-level logger: `logger = logging.getLogger(__name__)`
- Log at appropriate levels (debug, info, warning, error)
- Include context in log messages
- Don't log sensitive data

```python
logger = logging.getLogger("eva.module")

logger.debug("processing event: %s", event.id)
logger.info("task completed in %d ms", duration_ms)
logger.warning("file too large: %s (%s bytes)", path, size)
logger.error("failed to save snapshot: %s", e, exc_info=True)
```

## Testing

### Running Tests
```bash
# All tests
pytest -v

# Specific test file
pytest tests/test_chat_flow.py -v

# Specific test
pytest tests/test_chat_flow.py::test_chat_returns_reply -v

# With coverage
pytest --cov=app --cov=core --cov-report=term-missing
```

### Writing Tests
- Place tests in `tests/` directory
- Name test files `test_*.py`
- Use descriptive test names
- Each test should be independent
- Mock external dependencies

```python
def test_agent_returns_result():
    """Test that agent processes task and returns result."""
    task = AgentTask(kind="chat", payload={"text": "hello"})
    agent = ChatAgent()
    
    result = agent.run(task)
    
    assert result.ok is True
    assert "hello" in result.content.lower()
```

## Architecture

### Event Flow
1. User sends message via REST API
2. Event published to EventBus
3. CognitionLoop consumes event
4. Planner decides which agent to route to
5. AgentRouter selects agent
6. Orchestrator executes agent
7. Results stored in memory/trace
8. Response returned to user

### Key Components
- **app/** - FastAPI configuration and routes
- **core/** - Cognition loop, planner, proactive engine
- **event/** - Event definitions and bus
- **agents/** - Agent implementations
- **agent_os/** - Agent registry, router, orchestrator
- **memory/** - Persistence and memory management
- **runtime/** - Health, scheduling, logging

## Adding a New Agent

1. Create `agents/my_agent.py`:
```python
from agents.base_agent import AgentResult, AgentTask, BaseAgent

class MyAgent(BaseAgent):
    name = "my_agent"
    description = "What this agent does"
    
    def can_handle(self, task: AgentTask) -> bool:
        return task.kind == "my_kind"
    
    def run(self, task: AgentTask) -> AgentResult:
        # Implementation
        return AgentResult(...)
```

2. Register in `app/bootstrap.py`:
```python
from agents.my_agent import MyAgent
registry.register(MyAgent())
```

3. Update `core/planner.py` if needed for routing

4. Add tests in `tests/test_my_agent.py`

## Performance Optimization

### Profiling
```bash
# Use cProfile for CPU profiling
python -m cProfile -s cumulative app/main.py
```

### Key Metrics
- Event queue size (monitor pending_events)
- Agent execution time (in trace logs)
- Memory usage (episodic_memory table)
- Database query time (add indexes if needed)

### Tips
- Configure `queue_poll_timeout_sec` for responsiveness
- Tune `scheduler_*_interval_sec` based on load
- Adjust `result_ttl_sec` to manage cleanup frequency
- Monitor SQLite with `.schema` command

## Deployment

### Docker Build
```bash
docker build -t eva:latest .
```

### Docker Run
```bash
docker run -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -e EVA_ENV=production \
  eva:latest
```

### Docker Compose
```bash
docker-compose up -d
```

## Troubleshooting

### Tests Failing
- Check Python version (3.11+)
- Verify all dependencies installed
- Clear pytest cache: `pytest --cache-clear`

### Application Won't Start
- Check logs in `logs/eva.log`
- Verify `data/` directory is writable
- Check port 8000 is available

### Database Locked
- Ensure only one instance running
- Check for zombie processes
- Remove lock file if corrupted

## Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [Python Logging](https://docs.python.org/3/library/logging.html)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)
