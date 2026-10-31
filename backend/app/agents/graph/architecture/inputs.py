"""Validate resolved inputs and freeze the caller's profile/policies as JSON."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Resource

from contracts.agents.validate import ContractError, Contracts, load, loads

from .models import ArchitectureError, NODES, OUTPUTS, PreparedArchitecture, canonical, require

ROOT = Path(__file__).resolve().parents[5]


def uuid(value):
    try:
        require(str(UUID(value)) == value, "ARCHITECTURE_INVALID_IDENTITY")
    except (ValueError, TypeError, AttributeError):
        raise ArchitectureError("ARCHITECTURE_INVALID_IDENTITY") from None
    return value


def reference(artifact):
    return {key: deepcopy(value) for key, value in artifact.items() if key not in {"content", "storage_ref"}}


def prepare_architecture(context, requirements_artifact, *, profile_bytes, profile_checksum,
                         rules_text, rules_checksum, api_policy, naming_policy, resolved_inputs=()):
    """Inputs are authorized by the worker; no default profile is read on retry."""
    try:
        contracts = Contracts()
        context, requirements_artifact, extras = loads(canonical([context, requirements_artifact, list(resolved_inputs)]))
        contracts.validate("agent-input", context)
        require(type(profile_bytes) is bytes and sha256(profile_bytes).hexdigest() == profile_checksum,
                "ARCHITECTURE_PROFILE_CHECKSUM_MISMATCH")
        profile_content = profile_bytes.decode("utf-8")
        profile = loads(profile_content)
        from backend.app.agents.profiles.loader import validate_profile_data
        validate_profile_data(profile)
        require(context["profile_id"] == profile["profile_id"], "PROFILE_CONTEXT_MISMATCH")
        require(isinstance(rules_text, str) and bool(rules_text.strip())
                and sha256(rules_text.encode("utf-8")).hexdigest() == rules_checksum,
                "ARCHITECTURE_RULES_CHECKSUM_MISMATCH")
        artifacts = [requirements_artifact, *extras]
        require(len({a["artifact_id"] for a in artifacts}) == len(artifacts)
                and {a["artifact_id"] for a in artifacts} == set(context["input_artifact_ids"]),
                "ARCHITECTURE_UNRESOLVED_INPUT")
        for artifact in artifacts:
            contracts.validate("artifact", artifact)
            require("content" in artifact, "ARCHITECTURE_INPUT_NOT_RESOLVED")
            require(artifact["size_bytes"] <= profile["limits"]["artifacts"]["max_file_bytes"],
                    "ARCHITECTURE_INPUT_TOO_LARGE")
        require(requirements_artifact["path"] == "requirements.json"
                and requirements_artifact["kind"] == "requirements"
                and requirements_artifact["media_type"] == "application/json"
                and requirements_artifact["author_agent"] == "analysis", "ARCHITECTURE_REQUIREMENTS_REQUIRED")
        requirements = loads(requirements_artifact["content"])
        contracts.validate("requirements", requirements)
        grants = profile["ownership"]["agents"]["architecture"]
        require(all(path in grants["paths"] or any(path.startswith(d + "/") for d in grants["directories"])
                    for path in OUTPUTS.values()), "ARCHITECTURE_OUTPUT_NOT_ALLOWED")
        waiting = any(q["required"] and "answer" not in q for q in requirements["questions"])
        if not waiting:
            from .naming import validate_naming_policy
            validate_naming_policy(naming_policy, requirements_artifact["checksum"])
            require(isinstance(api_policy, dict)
                    and api_policy.get("requirements_checksum") == requirements_artifact["checksum"],
                    "ARCHITECTURE_API_POLICY_REQUIRED")
        limits = profile["limits"]["agent"]
        inputs = {
            "context": context, "requirements_artifact": requirements_artifact,
            "requirements": requirements, "resolved_inputs": extras,
            "profile_content": profile_content, "profile": profile, "profile_checksum": profile_checksum,
            "rules_text": rules_text, "rules_checksum": rules_checksum,
            "api_policy": api_policy, "naming_policy": naming_policy, "waiting": waiting,
            "max_calls": min(context["limits"]["max_calls"], limits["max_calls"]),
            "max_output_tokens": min(context["limits"]["max_output_tokens"], limits["max_output_tokens"]),
            "timeout_seconds": min(context["limits"].get("timeout_seconds", limits["timeout_seconds"]),
                                   limits["timeout_seconds"]),
        }
        return PreparedArchitecture(canonical(inputs))
    except ArchitectureError:
        raise
    except (ContractError, ValueError, TypeError, KeyError, UnicodeError):
        raise ArchitectureError("ARCHITECTURE_INVALID_INPUT") from None


def validate_reservation(reservation, inputs):
    require(isinstance(reservation, dict) and set(reservation) == {"attempt_id", "artifacts", "call_ids"},
            "ARCHITECTURE_INVALID_RESERVATION")
    require(reservation["artifacts"].keys() == OUTPUTS.keys()
            and reservation["call_ids"].keys() == set(NODES), "ARCHITECTURE_INVALID_RESERVATION")
    ids = [uuid(reservation["attempt_id"]), *[uuid(v) for v in reservation["call_ids"].values()]]
    for item in reservation["artifacts"].values():
        require(isinstance(item, dict) and set(item) == {"artifact_id", "version"}
                and type(item["version"]) is int and item["version"] >= 1, "ARCHITECTURE_INVALID_RESERVATION")
        ids.append(uuid(item["artifact_id"]))
    require(len(ids) == len(set(ids)) and not set(ids).intersection(inputs["context"]["input_artifact_ids"]),
            "ARCHITECTURE_IDENTITY_REUSED")
    return loads(canonical(reservation))
