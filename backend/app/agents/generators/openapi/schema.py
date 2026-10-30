"""The bounded model draft is separate from OpenAPI and the C-02 envelope."""

from copy import deepcopy

from contracts.agents.validate import Contracts

DRAFT_VERSION = "openapi-draft/1.0"


def draft_schema(contracts: Contracts) -> dict:
    common = contracts.schemas["common"]

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            prefix = common["$id"] + "#/$defs/"
            assert value["$ref"].startswith(prefix)
            return expand(common["$defs"][value["$ref"].removeprefix(prefix)])
        return {key: expand(item) for key, item in value.items()}

    def obj(properties):
        return {"type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}

    def array(items, minimum=0, maximum=100, unique=False):
        return {"type": "array", "items": items, "minItems": minimum,
                "maxItems": maximum, "uniqueItems": unique}

    text = deepcopy(common["$defs"]["text"])
    text["maxLength"] = 10000
    name = {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,63}$"}
    fr = deepcopy(common["$defs"]["fr_id"])
    role = {"type": "string", "pattern": "^ROLE-[0-9]{3,}$(?![\\s\\S])"}
    asm = {"type": "string", "pattern": "^ASM-[0-9]{3,}$(?![\\s\\S])"}
    json_schema = {"type": "object"}
    nullable_schema = {"anyOf": [json_schema, {"type": "null"}]}
    parameter = obj({
        "name": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_-]{0,127}$"},
        "in": {"enum": ["path", "query", "header"]},
        "required": {"type": "boolean"}, "description": text,
        "schema": json_schema,
    })
    operation = obj({
        "operation_id": name, "path": {"type": "string", "maxLength": 512},
        "method": {"enum": ["get", "post", "put", "patch", "delete"]},
        "summary": text, "description": text,
        "related_fr_ids": array(fr, 1, unique=True),
        "role_ids": array(role, unique=True),
        "authentication": {"enum": ["public", "bearer"]},
        "authorization_rules": array(text, unique=True),
        "assumption_ids": array(asm, unique=True),
        "integration_indices": array({"type": "integer", "minimum": 0}, unique=True),
        "parameters": array(parameter, maximum=50),
        "request_body": {"anyOf": [obj({
            "required": {"type": "boolean"}, "description": text,
            "schema": json_schema,
        }), {"type": "null"}]},
        "responses": array(obj({
            "status": {"type": "integer", "minimum": 200, "maximum": 299},
            "description": text, "schema": nullable_schema,
        }), 1, 10),
        "errors": array(obj({
            "status": {"type": "integer", "minimum": 400, "maximum": 599},
            "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,79}$"},
            "description": text,
        }), maximum=30),
    })
    schema = obj({
        "schema_version": {"const": DRAFT_VERSION},
        "title": text, "description": text,
        "schemas": {"type": "object", "propertyNames": name,
                    "additionalProperties": json_schema, "maxProperties": 100},
        "operations": array(operation, 1),
        "assumptions": array(expand(common["$defs"]["assumption"])),
        "coverage": array(obj({
            "fr_id": fr, "kind": {"enum": ["operation", "constraint", "outside_api"]},
            "operation_ids": array(name, unique=True), "reason": text,
        }), 1, 1000),
    })
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema
