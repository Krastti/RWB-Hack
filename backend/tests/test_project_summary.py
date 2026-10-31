"""PostgreSQL regressions for the project list read projection.

Run with RWB_TEST_DATABASE_URL pointing to a disposable UTF-8 PostgreSQL database.
Each test uses its own schema and applies the real migrations.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.models import User, Project, RequirementRevision, Run
from app.models.dto import ProjectPage
from app.repositories.postgresql import PostgreSQLProjectRepository


@unittest.skipUnless(os.environ.get("RWB_TEST_DATABASE_URL"), "requires disposable PostgreSQL")
class ProjectSummaryTests(unittest.TestCase):
    def setUp(self):
        self.schema = "pr100_" + uuid4().hex
        self.admin = create_engine(os.environ["RWB_TEST_DATABASE_URL"])
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(os.environ["RWB_TEST_DATABASE_URL"], connect_args={
            "options": f"-csearch_path={self.schema},public",
        })
        migrations = Path(__file__).resolve().parents[1] / "migrations"
        with self.engine.begin() as connection:
            for path in sorted(migrations.glob("V*__*.sql"), key=lambda p: int(p.name.split("__")[0][1:])):
                connection.exec_driver_sql(path.read_text())
        self.sessions = sessionmaker(self.engine)
        self.owner, self.other = uuid4(), uuid4()
        now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.projects = [uuid4() for _ in range(4)]
        revisions = [uuid4() for _ in self.projects]
        with self.sessions.begin() as session:
            session.add_all([User(id=self.owner, login="owner", password_hash="hash"),
                             User(id=self.other, login="other", password_hash="hash")])
            session.flush()
            session.add_all([Project(id=project, owner_id=self.owner if i < 3 else self.other,
                                     name=f"Project {i}", created_at=now + timedelta(seconds=i),
                                     updated_at=now) for i, project in enumerate(self.projects)])
            session.flush()
            session.add_all([RequirementRevision(id=revision, project_id=project, number=1,
                            requirements_text="Requirements", hash="a" * 64)
                            for project, revision in zip(self.projects, revisions)])
            session.flush()
            session.add_all([
                Run(id=UUID(int=3), project_id=self.projects[0], revision_id=revisions[0],
                    profile_id="web-fastapi-angular", status="completed",
                    limits={"max_calls": 10, "max_output_tokens": 30000}, created_at=now),
                Run(id=UUID(int=2), project_id=self.projects[0], revision_id=revisions[0],
                    profile_id="web-fastapi-angular", status="failed",
                    limits={"max_calls": 10, "max_output_tokens": 30000}, created_at=now),
                Run(id=UUID(int=4), project_id=self.projects[0], revision_id=revisions[0],
                    profile_id="web-fastapi-angular", status="cancelled",
                    limits={"max_calls": 10, "max_output_tokens": 30000}, created_at=now - timedelta(seconds=1)),
                Run(id=uuid4(), project_id=self.projects[3], revision_id=revisions[3],
                    profile_id="web-fastapi-angular", status="running",
                    limits={"max_calls": 10, "max_output_tokens": 30000}, created_at=now),
            ])
        self.repository = PostgreSQLProjectRepository(self.sessions, cursor_secret="x" * 48)
        self.selects = []
        event.listen(self.engine, "before_cursor_execute", self.record_select)

    def record_select(self, connection, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("SELECT"):
            self.selects.append(statement)

    def tearDown(self):
        self.engine.dispose()
        with self.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin.dispose()

    def test_list_embeds_latest_status_in_one_select_and_is_owner_scoped(self):
        page = self.repository.list(owner_id=self.owner, limit=100, cursor=None)
        self.assertEqual(len(self.selects), 1)
        items = {project.id: project for project in page.items}
        self.assertEqual(set(items), set(self.projects[:3]))
        self.assertEqual(items[self.projects[0]].latest_run_status, "completed")
        self.assertIsNone(items[self.projects[1]].latest_run_status)
        payload = json.loads(TypeAdapter(ProjectPage).dump_json(page))
        self.assertIn("latest_run_status", payload["items"][0])

    def test_detail_and_pagination_keep_the_projection_without_extra_queries(self):
        result = self.repository.get(owner_id=self.owner, project_id=self.projects[0])
        self.assertEqual(result.latest_run_status, "completed")
        self.assertEqual(len(self.selects), 1)
        self.assertIsNone(self.repository.get(owner_id=self.owner, project_id=self.projects[3]))
        cursor = None
        found = []
        for _ in range(3):
            self.selects.clear()
            page = self.repository.list(owner_id=self.owner, limit=1, cursor=cursor)
            self.assertEqual(len(self.selects), 1)
            found.extend(item.id for item in page.items)
            cursor = page.next_cursor
        self.assertIsNone(cursor)
        self.assertEqual(set(found), set(self.projects[:3]))


if __name__ == "__main__":
    unittest.main()
