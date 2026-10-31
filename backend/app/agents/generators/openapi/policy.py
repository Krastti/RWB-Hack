"""An architect-approved API surface; model output cannot approve its own grants."""

from copy import deepcopy
from hashlib import sha256
import json

from jsonschema import Draft202012Validator

from .errors import require
from .schema import draft_schema

POLICY_VERSION = "openapi-policy/1.0"


def policy_schema(contracts):
    draft = draft_schema(contracts)["properties"]
    operation = draft["operations"]["items"]["properties"]

    def obj(properties):
        return {"type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}

    indices = {"type": "array", "items": {"type": "integer", "minimum": 0},
               "uniqueItems": True, "maxItems": 1000}
    grant = obj({"role_id": operation["role_ids"]["items"],
                 "permission_indices": {**indices, "minItems": 1}})
    access = {key: operation[key] for key in (
        "operation_id", "path", "method", "related_fr_ids", "authentication", "authorization_rules")}
    access.update(grants={"type": "array", "items": grant, "maxItems": 100},
                  security_requirement_indices=indices)
    return obj({
        "schema_version": {"const": POLICY_VERSION},
        "requirements_checksum": {"type": "string", "pattern": r"^[a-f0-9]{64}$(?![\s\S])"},
        "operations": {"type": "array", "items": obj(access), "minItems": 1, "maxItems": 100},
        "coverage": draft["coverage"],
    })


def validate_api_policy(contracts, policy, requirements, checksum):
    """Validate a trusted input against this exact requirements snapshot, offline."""
    require(isinstance(policy, dict), "OPENAPI_POLICY_REQUIRED")
    require(Draft202012Validator(policy_schema(contracts)).is_valid(policy), "OPENAPI_INVALID_POLICY")
    require(policy["requirements_checksum"] == checksum, "OPENAPI_POLICY_SNAPSHOT_MISMATCH")
    frs = {fr["id"] for fr in requirements["functional_requirements"]}
    roles = {role["id"]: role for role in requirements["roles"]}
    operations, routes = {}, set()
    for operation in policy["operations"]:
        oid = operation["operation_id"]
        require(oid not in operations, "OPENAPI_INVALID_POLICY")
        operations[oid] = operation
        route = (operation["path"], operation["method"])
        require(route not in routes, "OPENAPI_INVALID_POLICY")
        routes.add(route)
        require(set(operation["related_fr_ids"]) <= frs, "OPENAPI_INVALID_POLICY")
        grants = operation["grants"]
        require(len({g["role_id"] for g in grants}) == len(grants), "OPENAPI_INVALID_POLICY")
        for grant in grants:
            role = roles.get(grant["role_id"])
            require(role is not None and all(type(i) is int and i < len(role["permissions"])
                    for i in grant["permission_indices"]), "OPENAPI_POLICY_UNKNOWN_PERMISSION")
        protected = operation["authentication"] == "bearer"
        require(bool(grants) and bool(operation["authorization_rules"]) if protected
                else not grants and not operation["authorization_rules"], "OPENAPI_INVALID_POLICY")
        for index in operation["security_requirement_indices"]:
            require(type(index) is int and index < len(requirements["security_requirements"]), "OPENAPI_INVALID_POLICY")
            require(requirements["security_requirements"][index]["text"] in operation["authorization_rules"],
                    "OPENAPI_POLICY_SECURITY_RULE_MISSING")
    coverage = {row["fr_id"]: row for row in policy["coverage"]}
    require(len(coverage) == len(policy["coverage"]) and coverage.keys() == frs, "OPENAPI_INVALID_POLICY")
    for fr, row in coverage.items():
        linked = set(row["operation_ids"])
        require(linked <= operations.keys()
                and (row["kind"] != "operation" or bool(linked))
                and (row["kind"] != "outside_api" or not linked)
                and linked == {oid for oid, op in operations.items() if fr in op["related_fr_ids"]},
                "OPENAPI_INVALID_POLICY")
    return deepcopy(policy)


def enforce_api_policy(draft, document, policy, requirements):
    require(policy is not None, "OPENAPI_POLICY_REQUIRED")
    expected = {operation["operation_id"]: operation for operation in policy["operations"]}
    actual = {operation["operation_id"]: operation for operation in draft["operations"]}
    require(actual.keys() == expected.keys(), "OPENAPI_POLICY_OPERATION_MISMATCH")
    roles = {role["id"]: role for role in requirements["roles"]}
    for oid, operation in actual.items():
        rule = expected[oid]
        require(all(operation[key] == rule[key] for key in ("path", "method"))
                and set(operation["related_fr_ids"]) == set(rule["related_fr_ids"]),
                "OPENAPI_POLICY_OPERATION_MISMATCH")
        require(operation["authentication"] == rule["authentication"]
                and set(operation["role_ids"]) == {grant["role_id"] for grant in rule["grants"]}
                and set(operation["authorization_rules"]) == set(rule["authorization_rules"]),
                "OPENAPI_POLICY_ACCESS_MISMATCH")
        output = document["paths"][operation["path"]][operation["method"]]
        output["x-role-permissions"] = [{
            "role_id": grant["role_id"], "permission_indices": list(grant["permission_indices"]),
            "permissions": [roles[grant["role_id"]]["permissions"][i] for i in grant["permission_indices"]],
        } for grant in rule["grants"]]
        output["x-security-requirement-indices"] = list(rule["security_requirement_indices"])
    expected_coverage = {row["fr_id"]: row for row in policy["coverage"]}
    for row in draft["coverage"]:
        approved = expected_coverage[row["fr_id"]]
        require(row["kind"] == approved["kind"]
                and set(row["operation_ids"]) == set(approved["operation_ids"]),
                "OPENAPI_POLICY_COVERAGE_MISMATCH")
    document["x-requirement-coverage"] = deepcopy(policy["coverage"])
    document["x-api-policy"] = {
        "schema_version": POLICY_VERSION,
        "checksum": sha256(json.dumps(policy, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
    }
