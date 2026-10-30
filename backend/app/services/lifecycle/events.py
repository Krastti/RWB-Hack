"""C-03 event allocation. Call only under the authoritative Run write lock."""
from copy import deepcopy
from .models import Event, utcnow


class EventSequenceError(ValueError):
    """Invalid authoritative history; caller maps to its domain error envelope."""


def _watermark(events, last_seq=None):
    try:
        sequences = [e.seq if isinstance(e, Event) else e["seq"] for e in events]
    except (KeyError, TypeError) as exc:
        raise EventSequenceError("Invalid event sequence") from exc
    previous = 0
    for seq in sequences:
        if type(seq) is not int or seq <= previous:
            raise EventSequenceError("Invalid event sequence")
        previous = seq
    maximum = previous
    if last_seq is not None and maximum != last_seq:
        raise EventSequenceError("Event history and sequence watermark disagree")
    return maximum


def next_event(*, run_id, events, event_type, data, stage_id=None, last_seq=None):
    maximum = _watermark(events, last_seq)
    return Event(run_id=run_id, seq=maximum + 1, event_type=event_type,
                 timestamp=utcnow(), stage_id=stage_id,
                 data=deepcopy({**data, "schema_version": "1.0"}))


class EventAllocator:
    """Transaction-local cursor, created under the Run lock; never persisted."""

    def __init__(self, events, last_seq=None):
        # Validate the complete history once, before any transaction effects.
        self.maximum = _watermark(events, last_seq)
        self.events = events
        self.length = len(events)

    def next(self, *, run_id, event_type, data, stage_id=None, last_seq=None):
        tail = self.events[-1] if self.events else None
        tail_seq = tail.seq if isinstance(tail, Event) else tail["seq"] if tail else 0
        if (len(self.events) != self.length or tail_seq != self.maximum or
                (last_seq is not None and last_seq != self.maximum)):
            raise EventSequenceError("Event history changed outside allocator")
        event = Event(run_id=run_id, seq=self.maximum + 1, event_type=event_type,
                      timestamp=utcnow(), stage_id=stage_id,
                      data=deepcopy({**data, "schema_version": "1.0"}))
        self.maximum = event.seq
        self.length += 1
        return event
