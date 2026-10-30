"""Offline regressions for the P1/P2 consistency fixes."""
import unittest
from hashlib import sha256
from unittest.mock import Mock
from uuid import uuid4

from app.models.dto.projects import ProjectCreate
from app.services.projects.service import ProjectService
from app.errors import InvalidProjectInput


class RequirementsWhitespaceTests(unittest.TestCase):
    def test_blank_rejected_before_repository_write(self):
        for value in ('', ' \t\n', '\u00a0', '\u0085\u2003\u3000', '\x1c'):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    ProjectCreate(name='Example', requirements_text=value)
                repository = Mock()
                with self.assertRaises(InvalidProjectInput):
                    ProjectService(repository).create_project(
                        owner_id=uuid4(), name='Example', requirements_text=value)
                repository.create.assert_not_called()

    def test_valid_text_and_hash_preserve_original_bytes(self):
        text = ' \tТребования\r\n\u00a0'
        repository = Mock()
        project = ProjectService(repository).create_project(
            owner_id=uuid4(), name='Example', requirements_text=text)
        self.assertEqual(project.current_revision.requirements_text, text)
        self.assertEqual(project.current_revision.hash, sha256(text.encode()).hexdigest())
        self.assertEqual(ProjectCreate(name='Example', requirements_text=text).requirements_text, text)


class WorkerImportsTests(unittest.TestCase):
    def test_agent_components_import_together(self):
        import importlib
        for module in ("app.agents.llm", "app.agents.prompts.analysis", "app.agents.graph"):
            self.assertIsNotNone(importlib.import_module(module))


class HistoricalProfileTests(unittest.TestCase):
    def test_read_preserves_historical_identity(self):
        from datetime import datetime, timezone
        from app.services.run_queries.dto import Run
        base = dict(run_id=uuid4(), project_id=uuid4(), revision_id=uuid4(),
                    status="completed", limits=dict(max_calls=1,max_output_tokens=1),
                    created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
                    stages=(), questions_revision=0, questions=(),
                    metrics=dict(tokens_in=0,tokens_out=0,model_calls=0,attempts=0,
                                 manual_clarifications=0,duration_ms=0))
        for profile in ("web-fastapi-angular", "web-fastapi-react"):
            run = Run(profile_id=profile, **base)
            self.assertEqual(run.model_dump()["profile_id"], profile)
        with self.assertRaises(ValueError):
            Run(profile_id="unsupported", **base)
