import pytest

from app.services.runtime.recovery import automatic_retry_count


def test_legacy_history_and_persisted_count_never_reset():
    events = [
        {"event_type": "retry.scheduled", "stage_id": "analysis", "data": {"mode": "automatic"}},
        {"event_type": "retry.scheduled", "stage_id": "analysis", "data": {"mode": "manual"}},
        {"event_type": "retry.scheduled", "stage_id": "other", "data": {"mode": "automatic"}},
    ]
    assert automatic_retry_count({}, events, "analysis") == 1
    assert automatic_retry_count({"auto_retry_counts": {"analysis": 2}}, events, "analysis") == 2
    with pytest.raises(ValueError):
        automatic_retry_count({"auto_retry_counts": {"analysis": -1}}, events, "analysis")


def test_ambiguous_history_is_not_treated_as_unused_budget():
    events = [{"event_type": "retry.scheduled", "stage_id": "analysis", "data": {}}]
    with pytest.raises(ValueError, match="Ambiguous"):
        automatic_retry_count({}, events, "analysis")
