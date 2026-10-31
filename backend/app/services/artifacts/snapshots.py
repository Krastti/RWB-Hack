"""Snapshot publication and owner-scoped C-01 byte retrieval.

Partial snapshots are inspectable, but cannot be downloaded as a prepared project.
Publication never changes Run.status or claims build/test verification.
"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
from uuid import UUID
from zipfile import ZipFile, ZIP_DEFLATED
from sqlalchemy import select
from contracts.agents.validate import Contracts
from ...models import Run, Project, StageAttempt
from .models import ArtifactObject, ArtifactSnapshot, ExecutionReceipt
from .store import require, ArtifactError


class SnapshotReadError(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def verified(row):
    raw, meta = row.content, row.metadata_json
    require(len(raw) == meta['size_bytes'] and sha256(raw).hexdigest() == meta['checksum'],
            'ARTIFACT_CHECKSUM_MISMATCH')
    path = meta['path']
    require(path and not path.startswith('/') and '\\' not in path and
            all(p not in {'', '.', '..'} for p in path.split('/')), 'ARTIFACT_UNSAFE_PATH')
    return raw


class SnapshotPublisher:
    def __init__(self, artifacts):
        self.artifacts = artifacts

    def publish(self, manifest):
        store = self.artifacts
        Contracts().validate('manifest', manifest)
        run_id, snapshot_id = UUID(manifest['run_id']), UUID(manifest['snapshot_id'])
        require(manifest['profile_id'] == store.profile.profile_id, 'SNAPSHOT_PROFILE_MISMATCH')
        require(store.owner_agent == 'assembly', 'SNAPSHOT_OWNER_REQUIRED')
        with store.execution.transaction() as session:
            store._writing(session, run_id, store.execution.lease.command.attempt_id)
            old = session.get(ArtifactSnapshot, snapshot_id)
            if old is not None:
                require(old.run_id == run_id and old.manifest == manifest, 'SNAPSHOT_ID_CONFLICT')
                return
            selected, paths = {}, set()
            for ref in manifest['artifacts']:
                row = session.get(ArtifactObject, UUID(ref['artifact_id']))
                require(row is not None and row.run_id == run_id and row.metadata_json == ref,
                        'SNAPSHOT_REFERENCE_MISMATCH')
                verified(row)
                require(ref['path'] not in paths and ref['artifact_id'] not in selected,
                        'SNAPSHOT_PATH_CONFLICT')
                paths.add(ref['path']); selected[ref['artifact_id']] = row
            require(not any(str(parent) in paths for path in paths for parent in PurePosixPath(path).parents),
                    'SNAPSHOT_PATH_CONFLICT')
            require('manifest.json' not in paths, 'SNAPSHOT_MANIFEST_SELF_REFERENCE')
            if manifest['readiness'] == 'prepared':
                required = {item['path']: item for item in store.profile.required_outputs(conditions=store.conditions)
                            if item['path'] != 'manifest.json'}
                actual = {r.metadata_json['path']: r for r in selected.values()}
                require(required.keys() <= actual.keys(), 'SNAPSHOT_REQUIRED_ARTIFACT_MISSING')
                for path, item in required.items():
                    require(actual[path].metadata_json['author_agent'] == item['owner'] and
                            actual[path].metadata_json['kind'] == item['kind'], 'SNAPSHOT_OWNERSHIP_MISMATCH')
                # Every upstream producer must have an accepted result and a
                # confirmed checkpoint. Merely having files is not acceptance.
                for row in selected.values():
                    if row.attempt_id == store.execution.lease.command.attempt_id:
                        continue
                    receipt = session.get(ExecutionReceipt, (row.attempt_id, 'result'))
                    checkpoint = session.get(ExecutionReceipt, (row.attempt_id, 'checkpoint'))
                    require(receipt is not None and receipt.payload['status'] == 'succeeded' and
                            checkpoint is not None and checkpoint.payload.get('result_checksum') ==
                            sha256(json.dumps(receipt.payload, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=False).encode()).hexdigest() and
                            any(a['artifact_id'] == str(row.artifact_id) for a in
                                receipt.payload['output_artifacts']), 'SNAPSHOT_CHECKPOINT_REQUIRED')
                req = json.loads(actual['requirements.json'].content)
                Contracts().validate('requirements', req)
                require(not any(q['required'] and 'answer' not in q for q in req['questions']),
                        'SNAPSHOT_UNRESOLVED_QUESTIONS')
                trace = actual['traceability.json']
                require(str(trace.artifact_id) == manifest['traceability_artifact_id'], 'SNAPSHOT_TRACEABILITY_MISMATCH')
                trace_doc = json.loads(trace.content)
                Contracts().validate('traceability', trace_doc)
                require(trace_doc['requirements_artifact_id'] == str(actual['requirements.json'].artifact_id)
                        and {c['fr_id'] for c in trace_doc['coverage']} ==
                            {r['id'] for r in req['functional_requirements']} and
                        len(trace_doc['coverage']) == len(req['functional_requirements']) and
                        all(set(c['artifact_ids']) <= selected.keys() for c in trace_doc['coverage']),
                        'SNAPSHOT_TRACEABILITY_MISMATCH')
            session.add(ArtifactSnapshot(snapshot_id=snapshot_id, run_id=run_id, manifest=deepcopy(manifest)))


class ArtifactReadService:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def owned(session, owner_id, run_id):
        row = session.scalar(select(Run).join(Project).where(Run.id == run_id, Project.owner_id == owner_id))
        if row is None:
            raise SnapshotReadError(404, 'NOT_FOUND')
        return row

    def accepted(self, *, owner_id, run_id):
        """Latest checkpoint-confirmed stage outputs, independently of assembly."""
        with self.sessions() as session:
            self.owned(session, owner_id, run_id)
            attempts = session.scalars(select(StageAttempt).where(StageAttempt.run_id == run_id,
                StageAttempt.status.in_(['succeeded', 'waiting_for_input'])))
            latest = {}
            for attempt in attempts:
                receipt = session.get(ExecutionReceipt, (attempt.id, 'result'))
                checkpoint = session.get(ExecutionReceipt, (attempt.id, 'checkpoint'))
                if receipt is None or checkpoint is None:
                    continue
                checksum = sha256(json.dumps(receipt.payload, sort_keys=True, separators=(',', ':'),
                    ensure_ascii=False).encode()).hexdigest()
                if checkpoint.payload.get('result_checksum') != checksum:
                    continue
                for ref in receipt.payload['output_artifacts']:
                    row = session.get(ArtifactObject, UUID(ref['artifact_id']))
                    require(row is not None and row.run_id == run_id and row.attempt_id == attempt.id,
                            'ARTIFACT_ACCEPTANCE_MISMATCH')
                    metadata = {k: v for k, v in ref.items() if k not in {'content', 'storage_ref'}}
                    require(row.metadata_json == metadata, 'ARTIFACT_ACCEPTANCE_MISMATCH')
                    if metadata['author_agent'] == 'orchestrator':
                        continue
                    verified(row)
                    old = latest.get(metadata['path'])
                    if old is None or (metadata['version'], metadata['artifact_id']) > (old['version'], old['artifact_id']):
                        latest[metadata['path']] = deepcopy(metadata)
            return {'items': [latest[path] for path in sorted(latest)]}

    def list(self, *, owner_id, run_id, limit=100, cursor=None):
        with self.sessions() as session:
            self.owned(session, owner_id, run_id)
            snapshots = list(session.scalars(select(ArtifactSnapshot).where(ArtifactSnapshot.run_id == run_id)))
            if not snapshots:
                raise SnapshotReadError(409, 'MANIFEST_NOT_READY')
            snap = max(snapshots, key=lambda s: (s.manifest['created_at'], str(s.snapshot_id)))
            # Cursor pins snapshot plus offset; later snapshots do not mix pages.
            offset = 0
            if cursor:
                try:
                    identity, position = cursor.split(':')
                    snap = session.get(ArtifactSnapshot, UUID(identity))
                    offset = int(position)
                    if snap is None or snap.run_id != run_id or offset < 0:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise SnapshotReadError(422, 'VALIDATION_ERROR') from None
            items = snap.manifest['artifacts']
            next_offset = offset + limit
            return {'snapshot_id': str(snap.snapshot_id), 'items': deepcopy(items[offset:next_offset]),
                    'next_cursor': f'{snap.snapshot_id}:{next_offset}' if next_offset < len(items) else None}

    def get(self, *, owner_id, run_id, artifact_id):
        with self.sessions() as session:
            self.owned(session, owner_id, run_id)
            row = session.get(ArtifactObject, artifact_id)
            if row is None or row.run_id != run_id:
                raise SnapshotReadError(404, 'NOT_FOUND')
            return deepcopy(row.metadata_json), verified(row)

    def archive(self, *, owner_id, run_id, snapshot_id):
        with self.sessions() as session:
            self.owned(session, owner_id, run_id)
            snap = session.get(ArtifactSnapshot, snapshot_id)
            if snap is None or snap.run_id != run_id:
                raise SnapshotReadError(404, 'NOT_FOUND')
            if snap.manifest['readiness'] != 'prepared':
                raise SnapshotReadError(409, 'MANIFEST_NOT_READY')
            stream = BytesIO()
            with ZipFile(stream, 'w', compression=ZIP_DEFLATED) as archive:
                for ref in snap.manifest['artifacts']:
                    row = session.get(ArtifactObject, UUID(ref['artifact_id']))
                    require(row is not None and row.run_id == run_id and row.metadata_json == ref,
                            'SNAPSHOT_REFERENCE_MISMATCH')
                    archive.writestr(ref['path'], verified(row))
                archive.writestr('manifest.json', json.dumps(snap.manifest, ensure_ascii=False, indent=2).encode())
            return stream.getvalue()
