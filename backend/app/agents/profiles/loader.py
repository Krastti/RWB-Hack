"""C-05 runtime policy. No network, database, queue or budget allocation."""
from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Resource
from referencing.exceptions import Unresolvable

from contracts.agents.validate import (
    ContractError, Contracts, load_profile_rules, loads, validate_profile_grants, read_policy_bytes,
)

ROOT = Path(__file__).resolve().parents[4]
MAX_POLICY_BYTES = 1024 * 1024
PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")


class ProfileError(ValueError):
    """A stable diagnostic code; messages contain no input content."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProfileError(code)


def _path(value: str, maximum: int = 512) -> None:
    _require(isinstance(value, str) and 0 < len(value) <= maximum
             and bool(PATH.fullmatch(value))
             and not any(part in {".", ".."} for part in value.split("/")), "PROFILE_INVALID_PATH")


def _covers(grant: str, directory: bool, path: str, *, target_directory: bool = False) -> bool:
    if directory:
        return path.startswith(grant + "/") or target_directory and path == grant
    return not target_directory and path == grant


def _parse(raw: bytes) -> dict:
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_POLICY_BYTES, "PROFILE_SIZE_LIMIT")
    try:
        value = loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ProfileError("PROFILE_INVALID_JSON") from None
    _require(isinstance(value, dict), "PROFILE_INVALID_JSON")
    return value


@dataclass(frozen=True)
class ProfileSource:
    """Only server code supplies these repository-relative allowlist entries."""
    profile_path: str
    schema_path: str


@dataclass(frozen=True)
class FrozenProfile:
    _raw: bytes = field(repr=False)
    _rules: bytes = field(repr=False)
    _root: Path = field(default=ROOT, repr=False, compare=False)

    @property
    def data(self) -> dict[str, Any]:
        """Detached copy; mutation never changes this snapshot."""
        return json.loads(self._raw)

    @property
    def profile_id(self) -> str:
        return self.data["profile_id"]

    @property
    def profile_version(self) -> str:
        return self.data["profile_version"]

    @property
    def checksum(self) -> str:
        return sha256(self._raw).hexdigest()

    @property
    def rules_text(self) -> str:
        return self._rules.decode("utf-8")

    @property
    def metadata(self) -> dict:
        return {"profile_id": self.profile_id, "profile_version": self.profile_version,
                "checksum": self.checksum, "rules_checksum": sha256(self._rules).hexdigest()}

    def snapshot(self) -> dict:
        """Private Run state, outside the closed C-02 AgentInput envelope."""
        return {"snapshot_version": "1.0", **self.metadata,
                "profile_json": self._raw.decode("utf-8"), "rules_text": self.rules_text}

    def grant(self, owner: str) -> dict:
        grants = self.data["ownership"]["agents"]
        _require(owner in grants, "PROFILE_UNKNOWN_OWNER")
        return grants[owner]

    def allows(self, owner: str, path: str, *, directory: bool = False, conditions: Mapping[str, bool] | None = None) -> bool:
        _path(path, self.data["limits"]["artifacts"]["max_path_length"])
        grant = self.grant(owner)
        for conditional in self.data["conditional_directories"]:
            if any(path == d or path.startswith(d + "/") for d in conditional["directories"]):
                if (conditions or {}).get(conditional["condition"]) is not True:
                    return False
        return (not directory and path in grant["paths"] or
                any(_covers(d, True, path, target_directory=directory) for d in grant["directories"]))

    def required_outputs(self, owner: str | None = None, *, conditions: Mapping[str, bool] | None = None) -> tuple[dict, ...]:
        if owner is not None:
            self.grant(owner)
        conditions = {} if conditions is None else conditions
        result = []
        for item in self.data["required_artifacts"]:
            if owner is not None and item["owner"] != owner:
                continue
            condition = item["condition"]
            if condition != "always":
                _require(condition in conditions and type(conditions[condition]) is bool,
                         "PROFILE_CONDITION_REQUIRED")
                if not conditions[condition]:
                    continue
            result.append(item)
        return tuple(result)

    def limits(self, owner: str) -> dict:
        """Return all policy scopes; caller reserves actual/effective budgets."""
        self.grant(owner)
        return self.data["limits"]

    def validate_plan(self, plan: dict, *, conditions: Mapping[str, bool] | None = None) -> None:
        try:
            Contracts(repository_root=self._root).validate("generation-plan", plan)
            validate_profile_grants(plan, self.data)
            for task in plan["tasks"]:
                for kind in ("output_paths", "output_directories"):
                    for path in task[kind]:
                        _require(self.allows(task["owner_agent"], path, directory=kind == "output_directories",
                                             conditions=conditions), "PROFILE_PLAN_MISMATCH")
        except (ContractError, KeyError, TypeError):
            raise ProfileError("PROFILE_PLAN_MISMATCH") from None


class ProfileLoader:
    def __init__(self, *, repository_root: Path = ROOT,
                 registry: Mapping[tuple[str, str], ProfileSource] | None = None,
                 defaults: Mapping[str, str] | None = None):
        self._root = Path(repository_root).resolve()
        self._registry = dict(registry) if registry is not None else {
            ("web-fastapi-angular", "1.0.0"): ProfileSource(
                "templates/profiles/web-fastapi-angular/profile.json",
                "templates/profiles/web-fastapi-angular/profile.schema.json")}
        self._defaults = dict(defaults) if defaults is not None else {"web-fastapi-angular": "1.0.0"}

    def _source(self, profile_id: str, version: str | None) -> tuple[str, ProfileSource]:
        _require(isinstance(profile_id, str) and (version is None or isinstance(version, str)),
                 "PROFILE_NOT_SUPPORTED")
        version = version if version is not None else self._defaults.get(profile_id)
        _require((profile_id, version) in self._registry, "PROFILE_NOT_SUPPORTED")
        return version, self._registry[(profile_id, version)]

    def _read(self, name: str) -> bytes:
        _path(name)
        path = self._root / name
        try:
            _require(path.resolve(strict=True) == path and path.is_file(), "PROFILE_UNTRUSTED_PATH")
            raw = read_policy_bytes(self._root, name, MAX_POLICY_BYTES)
        except ContractError as exc:
            raise ProfileError("PROFILE_SIZE_LIMIT" if str(exc) == "PROFILE_SIZE_LIMIT" else "PROFILE_UNTRUSTED_PATH") from None
        except OSError:
            raise ProfileError("PROFILE_UNAVAILABLE") from None
        _require(len(raw) <= MAX_POLICY_BYTES, "PROFILE_SIZE_LIMIT")
        return raw

    def _validate(self, raw: bytes, profile_id: str, version: str, source: ProfileSource) -> dict:
        profile = _parse(raw)
        schema = _parse(self._read(source.schema_path))
        try:
            Draft202012Validator.check_schema(schema)
            registry = Contracts(repository_root=self._root).registry.with_resource(schema["$id"], Resource.from_contents(schema))
            validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
            _require(validator.is_valid(profile), "PROFILE_SCHEMA_INVALID")
        except ProfileError:
            raise
        except (ValueError, KeyError, TypeError, SchemaError, Unresolvable):
            raise ProfileError("PROFILE_SCHEMA_INVALID") from None
        _require(profile["profile_id"] == profile_id and profile["profile_version"] == version,
                 "PROFILE_VERSION_MISMATCH")
        _validate_policy(profile)
        return profile

    def load(self, profile_id: str, *, version: str | None = None) -> FrozenProfile:
        version, source = self._source(profile_id, version)
        raw = self._read(source.profile_path)
        profile = self._validate(raw, profile_id, version, source)
        try:
            rules = load_profile_rules(profile, self._root).encode("utf-8")
        except ContractError as exc:
            raise ProfileError("PROFILE_SIZE_LIMIT" if str(exc) == "PROFILE_SIZE_LIMIT" else "PROFILE_RULES_UNAVAILABLE") from None
        except UnicodeError:
            raise ProfileError("PROFILE_RULES_UNAVAILABLE") from None
        _require(len(rules) <= MAX_POLICY_BYTES, "PROFILE_SIZE_LIMIT")
        return FrozenProfile(raw, rules, self._root)

    def restore(self, snapshot: dict, *, expected_checksum: str, expected_rules_checksum: str) -> FrozenProfile:
        """Restore saved bytes, never current defaults. Expected hashes are trusted Run metadata."""
        fields = {"snapshot_version", "profile_id", "profile_version", "checksum", "rules_checksum",
                  "profile_json", "rules_text"}
        _require(isinstance(snapshot, dict) and set(snapshot) == fields
                 and all(isinstance(v, str) for v in snapshot.values())
                 and snapshot["snapshot_version"] == "1.0", "PROFILE_SNAPSHOT_INVALID")
        _require(len(snapshot["profile_json"]) <= MAX_POLICY_BYTES
                 and len(snapshot["rules_text"]) <= MAX_POLICY_BYTES, "PROFILE_SIZE_LIMIT")
        try:
            raw, rules = snapshot["profile_json"].encode("utf-8"), snapshot["rules_text"].encode("utf-8")
        except UnicodeError:
            raise ProfileError("PROFILE_SNAPSHOT_INVALID") from None
        _require(0 < len(rules) <= MAX_POLICY_BYTES, "PROFILE_SIZE_LIMIT")
        _require(sha256(raw).hexdigest() == expected_checksum == snapshot["checksum"]
                 and sha256(rules).hexdigest() == expected_rules_checksum == snapshot["rules_checksum"],
                 "PROFILE_SNAPSHOT_CHANGED")
        version, source = self._source(snapshot["profile_id"], snapshot["profile_version"])
        self._validate(raw, snapshot["profile_id"], version, source)
        return FrozenProfile(raw, rules, self._root)


def _validate_policy(profile: dict) -> None:
    _require(profile["ownership"]["default"] == "deny", "PROFILE_OWNERSHIP_INVALID")
    grants = []
    maximum = profile["limits"]["artifacts"]["max_path_length"]
    for owner, grant in profile["ownership"]["agents"].items():
        for kind in ("paths", "directories"):
            for path in grant[kind]:
                _path(path, maximum)
                directory = kind == "directories"
                for previous, previous_directory, previous_owner in grants:
                    # Overlapping grants (even for one owner) obscure the least-privilege boundary.
                    overlap = (path == previous or path.startswith(previous + "/")
                               or previous.startswith(path + "/"))
                    _require(not overlap, "PROFILE_OWNERSHIP_CONFLICT")
                grants.append((path, directory, owner))
    seen = set()
    for item in profile["required_artifacts"]:
        path = item["path"]
        _path(path, maximum)
        _require(path not in seen, "PROFILE_DUPLICATE_OUTPUT")
        _require(not any(path.startswith(p + "/") or p.startswith(path + "/") for p in seen),
                 "PROFILE_OUTPUT_CONFLICT")
        seen.add(path)
        owners = {owner for grant, directory, owner in grants if _covers(grant, directory, path)}
        _require(owners == {item["owner"]}, "PROFILE_OUTPUT_OWNER_MISMATCH")
    for conditional in profile["conditional_directories"]:
        for path in conditional["directories"]:
            _path(path, maximum)
            _require(any(_covers(g, d, path, target_directory=True) for g, d, _ in grants),
                     "PROFILE_CONDITIONAL_OWNER_MISMATCH")
    limits = profile["limits"]
    _require(limits["agent"]["max_calls"] <= limits["run"]["max_calls"]
             and limits["agent"]["max_output_tokens"] <= limits["run"]["max_output_tokens"]
             and limits["model_call"]["timeout_seconds"] <= limits["agent"]["timeout_seconds"]
             <= limits["run"]["timeout_seconds"]
             and limits["agent"]["max_critical_questions"] <= limits["run"]["max_critical_questions_per_round"]
             and limits["artifacts"]["max_file_bytes"] <= limits["artifacts"]["max_total_bytes"],
             "PROFILE_LIMIT_CONFLICT")
