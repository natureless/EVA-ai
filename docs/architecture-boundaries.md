# EVA Module Boundaries

EVA has one stable runtime and one optional experimental extension. New
features should extend the stable runtime instead of creating parallel event,
memory, or cognition stacks.

## Stable Runtime

```text
app/main.py
  -> app/bootstrap.py          lifecycle ordering
  -> app/composition.py        typed component factories
  -> app/api_routes/*          HTTP adapters

event -> core -> agent_os -> agents
          |                    |
          +-> world + memory <-+
          +-> persona

runtime                            process services
connectors                         external event sources
```

## Rules

1. `app` may compose every stable subsystem.
2. Domain and runtime modules must not import `app`; configuration is injected.
3. Agents return results and do not call API routes or mutate the container.
4. File and network capabilities enter agents through guarded tools/executors.
5. `packages/*` is experimental MVSC code and enters through
   `app/experimental.py` only.
6. API routes read the typed `AppContainer` and publish events rather than
   constructing subsystems.

The rules are executable in `tests/test_architecture.py`.

## Adding Features

- Add a new external source under `connectors/` and publish stable events.
- Add a new decision rule or context transformation under `core/`.
- Add an agent under `agents/`, register it in `app/composition.py`, and inject
  its dependencies there.
- Add storage behavior behind `memory/storage_adapter.py`.
- Add experimental work under `packages/` only when it has a feature flag and
  a stable adapter in `app/experimental.py`.
