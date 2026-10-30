from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.lifecycle.models import LifecycleError
from app.services.runtime.lifecycle import execution_guard
from app.services.runtime.repository import PostgreSQLRuntimeRepository


class Snapshot:
    def __init__(self):
        self.data = None
        self.context = {"profile_id": "web-fastapi-angular"}

    def model_copy(self, *, deep):
        return self


def test_result_save_checks_same_domain_guard():
    attempt_id, run_id, owner_id = uuid4(), uuid4(), uuid4()
    old_stage = SimpleNamespace(stage_id="analysis", attempt_no=1, status="running")
    new_stage = SimpleNamespace(stage_id="analysis", attempt_no=1, status="succeeded")
    private = SimpleNamespace(stage_id="analysis", attempt_no=1, attempt_id=attempt_id)
    original = SimpleNamespace(run=SimpleNamespace(stages=[old_stage]), model_dump=lambda **_: {})
    proposed = SimpleNamespace(run=SimpleNamespace(stages=[new_stage]), attempts=[private],
                               owner_id=owner_id, model_dump=lambda **_: {"accepted": True})
    snapshot = Snapshot()

    class FakeLifecycleRepository:
        def __init__(self, *args):
            pass

        @contextmanager
        def transaction(self, **kwargs):
            yield snapshot

    repo = PostgreSQLRuntimeRepository(None, None)
    repo.guard = execution_guard
    with patch("app.services.runtime.lifecycle.BoundLifecycleRepository", FakeLifecycleRepository):
        with pytest.raises(LifecycleError, match="domain guard"):
            repo._guard_successes(None, SimpleNamespace(id=run_id), proposed, original)
        seen = []
        repo.guard = lambda state, change: seen.append((state.data.copy(), change))
        repo._guard_successes(None, SimpleNamespace(id=run_id), proposed, original)
    assert seen[0][0] == {"accepted": True}
    assert seen[0][1].attempt_id == attempt_id
    assert seen[0][1].target == "succeeded"
