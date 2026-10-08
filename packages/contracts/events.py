"""Compatibility imports for MVSC. Canonical contracts live in event/*.

The stable HTTP runtime uses Event's short type names through explicit codecs.
This import path remains available to existing experimental components.
"""
from event.contracts import EventEnvelope, EventFamily
from event.codec import LEGACY_EVENT_MAP, from_legacy_event

__all__ = ["EventEnvelope", "EventFamily", "LEGACY_EVENT_MAP", "from_legacy_event"]
