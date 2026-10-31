"""Manual C-02 fixture resolver; not a production artifact-store adapter."""
import json
from pathlib import Path

from . import ContextBuilder, ResolvedArtifact
from ..profiles import ProfileLoader

ROOT = Path(__file__).resolve().parents[4]
OWNER = "94ca2598-3f94-4f27-bf0a-85bbfdcb6811"


def fixture_inputs():
    artifacts = {}
    for name in ("agent-result.succeeded.json", "agent-result.architecture.json"):
        result = json.loads((ROOT / "contracts/agents/examples" / name).read_text())
        for artifact in result["output_artifacts"]:
            metadata = {key: value for key, value in artifact.items() if key not in {"content", "storage_ref"}}
            if "content" in artifact:
                artifacts[artifact["kind"]] = ResolvedArtifact(
                    OWNER, result["run_id"], result["contract_version"], True,
                    metadata, artifact["content"].encode("utf-8"))
    return artifacts


class FixtureResolver:
    def __init__(self, artifacts):
        self.artifacts = {a.metadata["artifact_id"]: a for a in artifacts}

    def resolve(self, *, owner_id, run_id, artifact_id):
        value = self.artifacts[artifact_id]
        if value.owner_id != owner_id or value.run_id != run_id:
            raise ValueError("Fixture owner/run mismatch")
        return value


def prepare_example(role):
    fixtures = fixture_inputs()
    kinds = ("requirements", "openapi", "ui_spec" if role == "frontend" else "data_model")
    artifacts = [fixtures[kind] for kind in kinds]
    request = {"schema_version": "1.0", "run_id": artifacts[0].run_id, "stage_id": role,
               "attempt": 1, "contract_version": artifacts[0].contract_version,
               "profile_id": "web-fastapi-angular", "limits": {"max_calls": 1, "max_output_tokens": 3000},
               "input_artifact_ids": [a.metadata["artifact_id"] for a in artifacts]}
    # These hand-authored common fixtures illustrate transport, not A-09 acceptance.
    builder = ContextBuilder(FixtureResolver(artifacts), max_context_bytes=256 * 1024)
    return builder.prepare(request, owner_id=OWNER, owner_agent=role,
                           profile=ProfileLoader().load("web-fastapi-angular"),
                           references={a.metadata["artifact_id"]: a.metadata for a in artifacts},
                           execution_inputs={"prompt": b"Use the accepted inputs for this task.",
                                             "template": b"", "model_configuration": b'{"model":"fixture"}',
                                             "compiler_configuration": b'{}'},
                           related_fr_ids=("FR-001",))


def main():
    print(json.dumps({role: prepare_example(role).manifest for role in ("frontend", "backend")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
