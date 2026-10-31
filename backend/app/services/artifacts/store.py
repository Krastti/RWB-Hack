"""PostgreSQL implementations of the existing graph, context and A-22 ports.

No external I/O and no files: BYTEA and receipt commit atomically under the
existing worker lease. Reads are owner scoped; every read rechecks stored bytes.
"""
from copy import deepcopy
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from contracts.agents.validate import Contracts
from ...models import Run, Project, StageAttempt
from ...agents.context import ResolvedArtifact
from ...agents.profiles import FrozenProfile
from .models import ArtifactObject, ExecutionReceipt


class ArtifactError(ValueError):
    pass


def require(value, code):
    if not value:
        raise ArtifactError(code)


def metadata(artifact):
    return {k: deepcopy(v) for k, v in artifact.items() if k not in {'content', 'storage_ref'}}


class PostgreSQLArtifactStore:
    def __init__(self, sessions, *, owner_id, execution, profile: FrozenProfile, conditions=None):
        self.sessions, self.owner_id = sessions, UUID(str(owner_id))
        self.execution, self.profile = execution, profile
        self.conditions = dict(conditions or {})
        self.contracts = Contracts()

    def _scope(self, session, run_id):
        run = session.scalar(select(Run).join(Project).where(
            Run.id == UUID(str(run_id)), Project.owner_id == self.owner_id))
        require(run is not None, 'ARTIFACT_NOT_FOUND')
        return run

    def _writing(self, session, run_id, attempt_id):
        self._scope(session, run_id)
        command = self.execution.lease.command
        require(command.run_id == UUID(str(run_id)) and command.attempt_id == UUID(str(attempt_id)),
                'ARTIFACT_SCOPE_MISMATCH')
        attempt = session.get(StageAttempt, command.attempt_id)
        require(attempt is not None and attempt.run_id == command.run_id, 'ARTIFACT_SCOPE_MISMATCH')

    def _validate(self, artifact):
        self.contracts.validate('artifact', artifact)
        require('content' in artifact and 'storage_ref' not in artifact, 'ARTIFACT_INLINE_REQUIRED')
        raw = artifact['content'].encode('utf-8')
        limits = self.profile.data['limits']['artifacts']
        require(len(raw) == artifact['size_bytes'] and sha256(raw).hexdigest() == artifact['checksum'],
                'ARTIFACT_CHECKSUM_MISMATCH')
        require(len(raw) <= limits['max_file_bytes'], 'ARTIFACT_SIZE_LIMIT')
        require(self.profile.allows(artifact['author_agent'], artifact['path'], conditions=self.conditions),
                'ARTIFACT_GRANT_DENIED')
        return raw

    def _put(self, session, run_id, artifact):
        self._writing(session, run_id, artifact['attempt_id'])
        raw = self._validate(artifact)
        identity = UUID(artifact['artifact_id'])
        old = session.get(ArtifactObject, identity)
        if old is not None:
            require(old.run_id == UUID(str(run_id)) and old.metadata_json == metadata(artifact)
                    and old.content == raw, 'ARTIFACT_ID_CONFLICT')
            return
        rows = list(session.scalars(select(ArtifactObject).where(ArtifactObject.run_id == UUID(str(run_id)))))
        limits = self.profile.data['limits']['artifacts']
        require(len(rows) < limits['max_files'] and sum(len(r.content) for r in rows) + len(raw)
                <= limits['max_total_bytes'], 'ARTIFACT_RUN_SIZE_LIMIT')
        require(not any(r.metadata_json['path'] == artifact['path'] and
                        r.metadata_json['version'] == artifact['version'] for r in rows),
                'ARTIFACT_VERSION_CONFLICT')
        session.add(ArtifactObject(artifact_id=identity, run_id=UUID(str(run_id)),
            attempt_id=UUID(artifact['attempt_id']), metadata_json=metadata(artifact), content=raw))
        session.flush()

    def put(self, run_id, artifact):
        with self.execution.transaction() as session:
            self._put(session, run_id, artifact)

    def read(self, run_id, artifact_id):
        with self.sessions() as session:
            self._scope(session, run_id)
            row = session.get(ArtifactObject, UUID(str(artifact_id)))
            require(row is not None and row.run_id == UUID(str(run_id)), 'ARTIFACT_NOT_FOUND')
            artifact = {**deepcopy(row.metadata_json), 'content': row.content.decode('utf-8')}
            self._validate(artifact)
            return artifact

    def resolve(self, *, owner_id, run_id, artifact_id):
        require(UUID(str(owner_id)) == self.owner_id, 'ARTIFACT_NOT_FOUND')
        artifact = self.read(run_id, artifact_id)
        with self.sessions() as session:
            row = session.get(StageAttempt, UUID(artifact['attempt_id']))
            version = row.contract_version
        return ResolvedArtifact(str(self.owner_id), str(run_id), version, True,
                                metadata(artifact), artifact['content'].encode('utf-8'))

    def _receipt(self, session, run_id, attempt_id, key):
        self._scope(session, run_id)
        attempt = session.get(StageAttempt, UUID(str(attempt_id)))
        require(attempt is not None and attempt.run_id == UUID(str(run_id)), 'ARTIFACT_NOT_FOUND')
        row = session.get(ExecutionReceipt, (attempt.id, key))
        return deepcopy(row.payload) if row else None

    def _save_receipt(self, session, run_id, attempt_id, key, payload):
        self._writing(session, run_id, attempt_id)
        old = self._receipt(session, run_id, attempt_id, key)
        require(old is None or old == payload, 'ARTIFACT_RECEIPT_CONFLICT')
        if old is None:
            session.add(ExecutionReceipt(attempt_id=UUID(str(attempt_id)), key=key, payload=deepcopy(payload)))

    def result(self, run_id, attempt_id):
        with self.sessions() as session:
            return self._receipt(session, run_id, attempt_id, 'result')

    def accept(self, run_id, attempt_id, result):
        self.contracts.validate('agent-result', result)
        require(result['run_id'] == str(run_id), 'ARTIFACT_SCOPE_MISMATCH')
        with self.execution.transaction() as session:
            self._writing(session, run_id, attempt_id)
            for artifact in result['output_artifacts']:
                require(artifact['attempt_id'] == str(attempt_id), 'ARTIFACT_SCOPE_MISMATCH')
                self._put(session, run_id, artifact)
            self._save_receipt(session, run_id, attempt_id, 'result', result)


class PostgreSQLBatchStore:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def _owner(self, owner_id):
        require(UUID(str(owner_id)) == self.artifacts.owner_id, 'ARTIFACT_NOT_FOUND')

    def open_attempt(self, *, owner_id, run_id, attempt_id, fingerprint):
        self._owner(owner_id)
        with self.artifacts.execution.transaction() as session:
            self.artifacts._save_receipt(session, run_id, attempt_id, 'inventory', {'fingerprint': fingerprint})

    def receipt(self, *, owner_id, run_id, attempt_id, batch_id):
        self._owner(owner_id)
        with self.artifacts.sessions() as session:
            return self.artifacts._receipt(session, run_id, attempt_id, 'batch:' + batch_id)

    def accept(self, *, owner_id, run_id, attempt_id, batch_id, receipt):
        self._owner(owner_id)
        with self.artifacts.execution.transaction() as session:
            require(self.artifacts._receipt(session, run_id, attempt_id, 'inventory') is not None,
                    'ARTIFACT_INVENTORY_REQUIRED')
            for artifact in receipt['artifacts']:
                require(artifact['attempt_id'] == str(attempt_id), 'ARTIFACT_SCOPE_MISMATCH')
                self.artifacts._put(session, run_id, artifact)
            self.artifacts._save_receipt(session, run_id, attempt_id, 'batch:' + batch_id, receipt)
