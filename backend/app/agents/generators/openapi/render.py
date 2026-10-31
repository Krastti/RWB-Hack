"""Deterministic OpenAPI 3.1 compiler; never resolves network or file references."""

from copy import deepcopy
import json
import math
import re

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import OpenAPIV31SpecValidator
import yaml

from .schema import DRAFT_VERSION
from .errors import OpenAPIError, require
from .policy import enforce_api_policy

GENERATOR_VERSION = "1.1.0"
MAX_ARTIFACT_BYTES = 1048576
_SEGMENT = r"(?:[A-Za-z0-9_-]+|\{[A-Za-z_][A-Za-z0-9_]*\})"
_PATH = re.compile(r"^/(?:" + _SEGMENT + r"(?:/" + _SEGMENT + r")*)?$")
_ERROR = {
    "type": "object", "additionalProperties": False, "required": ["error"],
    "properties": {"error": {
        "type": "object", "additionalProperties": False,
        "required": ["code", "message", "details", "request_id"],
        "properties": {
            "code": {"type": "string", "minLength": 1},
            "message": {"type": "string", "minLength": 1},
            "details": {"type": "object", "additionalProperties": True},
            "request_id": {"type": "string", "minLength": 1},
        },
    }},
}


def _nodes(value):
    stack = [(value, 0)]
    count = 0
    while stack:
        item, depth = stack.pop()
        count += 1
        require(depth <= 32 and count <= 20000, "OPENAPI_DRAFT_TOO_COMPLEX")
        yield item
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        stack.extend((child, depth + 1) for child in children)


def _schema_nodes(schema):
    """Visit subschemas, not user field names, enum values or example objects."""
    yield schema
    if not isinstance(schema, dict):
        return
    for key in ("properties", "patternProperties", "$defs", "dependentSchemas"):
        for child in schema.get(key, {}).values():
            yield from _schema_nodes(child)
    for key in ("items", "additionalProperties", "contains", "propertyNames", "not",
                "if", "then", "else", "unevaluatedItems", "unevaluatedProperties", "contentSchema"):
        if key in schema:
            yield from _schema_nodes(schema[key])
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(key, []):
            yield from _schema_nodes(child)


def _check_schemas(document, prepared, assumptions):
    """Only root-local component JSON Pointers, with no URI rebasing keywords."""
    roots = list(document["components"]["schemas"].values())
    for path in document["paths"].values():
        for operation in path.values():
            roots.extend(p["schema"] for p in operation["parameters"])
            if "requestBody" in operation:
                roots.append(operation["requestBody"]["content"]["application/json"]["schema"])
            for response in operation["responses"].values():
                if "content" in response:
                    roots.append(response["content"]["application/json"]["schema"])
    for schema in roots:
        try:
            Draft202012Validator.check_schema(schema)
        except Exception:
            raise OpenAPIError("OPENAPI_INVALID_DTO_SCHEMA") from None
    for item in (node for schema in roots for node in _schema_nodes(schema)):
        if not isinstance(item, dict):
            continue
        require(not {"$id", "$schema", "$dynamicRef", "$recursiveRef", "$anchor",
                     "$dynamicAnchor"}.intersection(item), "OPENAPI_UNSUPPORTED_SCHEMA_KEYWORD")
        if "$ref" in item:
            ref = item["$ref"]
            require(isinstance(ref, str) and re.fullmatch(r"#/components/schemas/[A-Za-z_][A-Za-z0-9_]{0,63}", ref),
                    "OPENAPI_EXTERNAL_REFERENCE")
            require(ref.rsplit("/", 1)[1] in document["components"]["schemas"],
                    "OPENAPI_UNRESOLVED_REFERENCE")
        for field, known in (("x-fr-ids", {f["id"] for f in prepared.requirements["functional_requirements"]}),
                             ("x-assumption-ids", assumptions)):
            if field in item:
                ids = item[field]
                require(isinstance(ids, list) and all(isinstance(i, str) for i in ids)
                        and len(set(ids)) == len(ids) and set(ids) <= known, "OPENAPI_UNKNOWN_SCHEMA_PROVENANCE")
        for lower, upper in (("minLength", "maxLength"), ("minItems", "maxItems"),
                             ("minProperties", "maxProperties")):
            if lower in item and upper in item:
                require(item[lower] <= item[upper], "OPENAPI_EMPTY_FIELD_RANGE")
        lower_bounds = [(item[key], key == "exclusiveMinimum")
                        for key in ("minimum", "exclusiveMinimum") if key in item]
        upper_bounds = [(item[key], key == "exclusiveMaximum")
                        for key in ("maximum", "exclusiveMaximum") if key in item]
        for lower, lower_open in lower_bounds:
            for upper, upper_open in upper_bounds:
                require(lower < upper or lower == upper and not (lower_open or upper_open),
                        "OPENAPI_EMPTY_FIELD_RANGE")
                if item.get("type") in ("integer", ["integer"]):
                    first = math.floor(lower) + 1 if lower_open else math.ceil(lower)
                    last = math.ceil(upper) - 1 if upper_open else math.floor(upper)
                    require(first <= last, "OPENAPI_EMPTY_FIELD_RANGE")


def compile_openapi(draft, prepared):
    """Validate draft, links and coverage, then compile the supported HTTP subset."""
    try:
        raw = json.dumps(draft, ensure_ascii=False, allow_nan=False).encode("utf-8")
        require(len(raw) <= prepared.max_artifact_bytes, "OPENAPI_ARTIFACT_TOO_LARGE")
        list(_nodes(draft))
        validator = Draft202012Validator(prepared.schema, format_checker=FormatChecker())
        require(not any(validator.iter_errors(draft)), "OPENAPI_INVALID_DRAFT")
        return _compile(draft, prepared)
    except OpenAPIError:
        raise
    except Exception:
        raise OpenAPIError("OPENAPI_INVALID_DRAFT") from None


def _compile(draft, prepared):
    requirements = prepared.requirements
    frs = {item["id"] for item in requirements["functional_requirements"]}
    roles = {item["id"] for item in requirements["roles"]}
    inherited = {item["id"]: deepcopy(item) for item in requirements["assumptions"]}
    seen = set()
    for assumption in draft["assumptions"]:
        aid = assumption["id"]
        require(aid not in seen, "OPENAPI_DUPLICATE_ASSUMPTION")
        seen.add(aid)
        require(set(assumption["related_fr_ids"]) <= frs, "OPENAPI_UNKNOWN_FR")
        require(aid not in inherited or inherited[aid] == assumption, "OPENAPI_ASSUMPTION_REDEFINED")
        inherited[aid] = deepcopy(assumption)
    next_id = max((int(a.removeprefix("ASM-")) for a in inherited), default=0) + 1
    inherited[f"ASM-{next_id:03}"] = {
        "id": f"ASM-{next_id:03}",
        "text": "API использует OpenAPI 3.1.0, JSON, единый Error envelope и относительный /api/v1; bearer JWT применяется только к отмеченным операциям.",
        "reason": "Технические соглашения генератора A-06, не требования заказчика; способ выдачи токенов согласует архитектор.",
        "related_fr_ids": sorted(frs),
    }
    schemas = deepcopy(draft["schemas"])
    require("Error" not in schemas, "OPENAPI_RESERVED_SCHEMA_NAME")
    schemas["Error"] = deepcopy(_ERROR)
    document = {
        "openapi": "3.1.0", "info": {"title": draft["title"],
            "description": draft["description"], "version": prepared.context["contract_version"]},
        "servers": [{"url": "/api/v1"}], "paths": {},
        "components": {"schemas": schemas},
        "x-generator": {"name": "rwb-openapi", "version": GENERATOR_VERSION,
                        "draft_schema_version": DRAFT_VERSION},
        "x-requirements-artifact-id": prepared.requirements_artifact_id,
        "x-profile-id": prepared.context["profile_id"],
        "x-assumptions": list(inherited.values()),
        "x-requirement-coverage": deepcopy(draft["coverage"]),
        "x-roles": deepcopy(requirements["roles"]),
        "x-constraints": {"business": deepcopy(requirements["constraints"]),
                          "security": deepcopy(requirements["security_requirements"]),
                          "nonfunctional": deepcopy(requirements["nonfunctional_requirements"])},
    }
    operations, route_shapes, integrations_used = {}, {}, set()
    for item in draft["operations"]:
        path, method, oid = item["path"], item["method"], item["operation_id"]
        require(_PATH.fullmatch(path), "OPENAPI_INVALID_PATH")
        shape = re.sub(r"\{[^{}]+\}", "{}", path)
        require(shape not in route_shapes or route_shapes[shape] == path, "OPENAPI_AMBIGUOUS_PATH")
        route_shapes[shape] = path
        require(oid not in operations, "OPENAPI_DUPLICATE_OPERATION_ID")
        route = document["paths"].setdefault(path, {})
        require(method not in route, "OPENAPI_DUPLICATE_ROUTE")
        require(set(item["related_fr_ids"]) <= frs, "OPENAPI_UNKNOWN_FR")
        require(set(item["role_ids"]) <= roles, "OPENAPI_UNKNOWN_ROLE")
        require(set(item["assumption_ids"]) <= inherited.keys(), "OPENAPI_UNKNOWN_ASSUMPTION")
        require(all(i < len(requirements["integrations"]) for i in item["integration_indices"]),
                "OPENAPI_INVENTED_INTEGRATION")
        integrations_used.update(item["integration_indices"])
        protected = item["authentication"] == "bearer"
        require(protected or not item["role_ids"] and not item["authorization_rules"],
                "OPENAPI_PUBLIC_AUTHORIZATION_CONFLICT")
        require(not protected or bool(item["authorization_rules"]), "OPENAPI_MISSING_AUTHORIZATION")
        if protected:
            document["components"]["securitySchemes"] = {
                "BearerJWT": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
        parameters, keys, path_parameters = [], set(), set()
        for parameter in item["parameters"]:
            name, location = parameter["name"], parameter["in"]
            key = (location, name.casefold() if location == "header" else name)
            require(key not in keys, "OPENAPI_DUPLICATE_PARAMETER")
            keys.add(key)
            require(location != "header" or name.casefold() not in {
                "authorization", "content-type", "accept", "host", "cookie"}, "OPENAPI_RESERVED_HEADER")
            if location == "path":
                require(parameter["required"], "OPENAPI_OPTIONAL_PATH_PARAMETER")
                path_parameters.add(name)
            parameters.append(deepcopy(parameter))
        require(path_parameters == set(re.findall(r"\{([^{}]+)\}", path)), "OPENAPI_PATH_PARAMETER_MISMATCH")
        operation = {"operationId": oid, "summary": item["summary"],
            "description": item["description"], "parameters": parameters,
            "security": [{"BearerJWT": []}] if protected else [],
            "x-fr-ids": item["related_fr_ids"], "x-role-ids": item["role_ids"],
            "x-authorization-rules": item["authorization_rules"],
            "x-assumption-ids": item["assumption_ids"],
            "x-integration-indices": item["integration_indices"], "responses": {}}
        body = item["request_body"]
        if body is not None:
            require(method in {"post", "put", "patch"}, "OPENAPI_UNSUPPORTED_REQUEST_BODY")
            operation["requestBody"] = {"required": body["required"], "description": body["description"],
                "content": {"application/json": {"schema": deepcopy(body["schema"])}}}
        for response in item["responses"]:
            status = str(response["status"])
            require(status not in operation["responses"], "OPENAPI_DUPLICATE_RESPONSE")
            require(status != "204" or response["schema"] is None, "OPENAPI_204_WITH_BODY")
            entry = {"description": response["description"]}
            if response["schema"] is not None:
                entry["content"] = {"application/json": {"schema": deepcopy(response["schema"])}}
            operation["responses"][status] = entry
        errors = {422: {"VALIDATION_ERROR"}, 500: {"INTERNAL_ERROR"}}
        if protected:
            errors.update({401: {"UNAUTHORIZED"}, 403: {"FORBIDDEN"}})
        for error in item["errors"]:
            require(error["status"] not in {401, 403} or protected, "OPENAPI_PUBLIC_AUTHORIZATION_CONFLICT")
            errors.setdefault(error["status"], set()).add(error["code"])
        for status, codes in sorted(errors.items()):
            entry = {"description": "; ".join(e["description"] for e in item["errors"] if e["status"] == status)
                     or ", ".join(sorted(codes)), "x-error-codes": sorted(codes),
                     "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
            if status == 429:
                entry["headers"] = {"Retry-After": {"description": "Секунды до повтора запроса.",
                                                   "schema": {"type": "integer", "minimum": 1}}}
            operation["responses"][str(status)] = entry
        route[method], operations[oid] = operation, item
    coverage = {row["fr_id"]: row for row in draft["coverage"]}
    require(len(coverage) == len(draft["coverage"]) and coverage.keys() == frs, "OPENAPI_INCOMPLETE_FR_COVERAGE")
    for fr, row in coverage.items():
        linked = set(row["operation_ids"])
        require(linked <= operations.keys(), "OPENAPI_UNKNOWN_OPERATION")
        require(row["kind"] != "operation" or bool(linked), "OPENAPI_INCOMPLETE_FR_COVERAGE")
        require(row["kind"] != "outside_api" or not linked, "OPENAPI_INVALID_FR_COVERAGE")
        require(linked == {oid for oid, op in operations.items() if fr in op["related_fr_ids"]},
                "OPENAPI_FR_LINK_MISMATCH")
    document["x-integrations"] = [{"index": i, **deepcopy(requirements["integrations"][i])}
                                   for i in sorted(integrations_used)]
    enforce_api_policy(draft, document, prepared.api_policy, requirements)
    _check_schemas(document, prepared, inherited.keys())
    try:
        OpenAPIV31SpecValidator(document).validate()
    except Exception:
        raise OpenAPIError("OPENAPI_INVALID_SPECIFICATION") from None
    return document


def render_openapi(draft, prepared) -> str:
    document = compile_openapi(draft, prepared)
    content = yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=100)
    require(len(content.encode("utf-8")) <= prepared.max_artifact_bytes, "OPENAPI_ARTIFACT_TOO_LARGE")
    return content
