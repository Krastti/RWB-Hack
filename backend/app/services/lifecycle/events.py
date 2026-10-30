"""C-03 event allocation. Call only under the authoritative Run write lock."""
from copy import deepcopy
from .models import Event, utcnow


class EventSequenceError(ValueError):
    """Invalid authoritative history; caller maps to its domain error envelope."""


def next_event(*, run_id, events, event_type, data, stage_id=None, last_seq=None):
    try:
        sequences = [e.seq if isinstance(e, Event) else e["seq"] for e in events]
    except (KeyError, TypeError) as exc:
        raise EventSequenceError("Invalid event sequence") from exc
    if any(type(seq) is not int or seq < 1 for seq in sequences) or sequences != sorted(set(sequences)):
        raise EventSequenceError("Invalid event sequence")
    maximum = sequences[-1] if sequences else 0
    if last_seq is not None and maximum != last_seq:
        raise EventSequenceError("Event history and sequence watermark disagree")
    return Event(run_id=run_id, seq=maximum + 1, event_type=event_type,
                 timestamp=utcnow(), stage_id=stage_id,
                 data=deepcopy({**data, "schema_version": "1.0"}))
