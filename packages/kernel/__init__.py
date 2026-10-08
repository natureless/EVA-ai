"""packages/kernel 包 — EVA-MVSC 运行时内核。"""

from packages.kernel.event_store import (
    DuplicateEventError,
    EventStore,
    VersionConflictError,
)
from packages.kernel.event_bus_adapter import (
    EventBusAdapter,
    from_legacy_event,
    to_legacy_event,
)
from packages.kernel.state_bridge import (
    conscious_to_system_state,
    sync_system_state,
    system_state_to_conscious,
)
from packages.kernel.state_repository import InMemoryStateRepository
from packages.kernel.sqlite_state_repository import SQLiteStateRepository
from packages.kernel.action_dispatcher import ActionDispatcher
from packages.kernel.inbox_outbox import (
    DurableInboxOutbox,
    InboxClaim,
    OutboxClaim,
)
from packages.kernel.episode_store import DuplicateEpisodeError, EpisodeStore
from packages.kernel.mvsc_bootstrap import (
    integrate_mvsc,
    shutdown_mvsc,
)

__all__ = [
    "DuplicateEventError",
    "EventStore",
    "VersionConflictError",
    "EventBusAdapter",
    "from_legacy_event",
    "to_legacy_event",
    "conscious_to_system_state",
    "sync_system_state",
    "system_state_to_conscious",
    "InMemoryStateRepository",
    "SQLiteStateRepository",
    "ActionDispatcher",
    "DurableInboxOutbox",
    "InboxClaim",
    "OutboxClaim",
    "DuplicateEpisodeError",
    "EpisodeStore",
    "integrate_mvsc",
    "shutdown_mvsc",
]
