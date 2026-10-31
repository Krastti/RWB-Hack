"""Explicit API↔storage names; never guess a rename from case or plurals."""

from jsonschema import Draft202012Validator

from .models import require


def naming_policy_schema():
    name = {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,63}$(?![\s\S])"}
    reason = {"type": "string", "minLength": 1, "maxLength": 4000, "pattern": r"\S"}

    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

    def mapping(value):
        return {"type": "object", "propertyNames": name, "additionalProperties": value, "maxProperties": 1000}

    binding = obj({"schema": name, "entity": name, "fields": mapping(name), "derived_fields": mapping(reason)})
    return obj({
        "schema_version": {"const": "architecture-names/1.0"},
        "requirements_checksum": {"type": "string", "pattern": r"^[a-f0-9]{64}$(?![\s\S])"},
        "bindings": {"type": "array", "items": binding, "minItems": 1, "maxItems": 100},
        "nonpersistent_schemas": mapping(reason), "unexposed_entities": mapping(reason),
    })


def validate_naming_policy(policy, checksum):
    require(Draft202012Validator(naming_policy_schema()).is_valid(policy), "ARCHITECTURE_NAMING_POLICY_REQUIRED")
    require(policy["requirements_checksum"] == checksum, "ARCHITECTURE_NAMING_SNAPSHOT_MISMATCH")
    names = [item["schema"] for item in policy["bindings"]]
    require(len(names) == len(set(names)) and not set(names).intersection(policy["nonpersistent_schemas"]),
            "ARCHITECTURE_INVALID_NAMING_POLICY")
    for item in policy["bindings"]:
        require(not item["fields"].keys() & item["derived_fields"].keys()
                and len(set(item["fields"].values())) == len(item["fields"]), "ARCHITECTURE_INVALID_NAMING_POLICY")


def validate_names(document, model, policy):
    schemas = document["components"]["schemas"]
    entities = {entity["name"]: entity for entity in model["entities"]}
    expected_schemas = {item["schema"] for item in policy["bindings"]} | policy["nonpersistent_schemas"].keys()
    expected_entities = {item["entity"] for item in policy["bindings"]} | policy["unexposed_entities"].keys()
    require(schemas.keys() == expected_schemas, "ARCHITECTURE_SCHEMA_NAME_MISMATCH",
            "Архитектор: имена DTO отличаются от утверждённой карты API/data; требуется пересогласование.")
    require(entities.keys() == expected_entities, "ARCHITECTURE_ENTITY_NAME_MISMATCH",
            "Архитектор: имена сущностей отличаются от утверждённой карты API/data; требуется пересогласование.")
    require(not {item["entity"] for item in policy["bindings"]}.intersection(policy["unexposed_entities"]),
            "ARCHITECTURE_INVALID_NAMING_POLICY")
    kinds = {
        "uuid": ("string", "uuid"), "text": ("string", None), "varchar": ("string", None),
        "integer": ("integer", None), "bigint": ("integer", None), "boolean": ("boolean", None),
        "numeric": ("number", None), "date": ("string", "date"), "timestamptz": ("string", "date-time"),
        "jsonb": ("object", None),
    }
    create_schemas = set()
    for path in document.get("paths", {}).values():
        for method in ("post", "put"):
            operation = path.get(method, {})
            for media in operation.get("requestBody", {}).get("content", {}).values():
                ref = media.get("schema", {}).get("$ref", "")
                if ref.startswith("#/components/schemas/"):
                    create_schemas.add(ref.removeprefix("#/components/schemas/"))
    for binding in policy["bindings"]:
        schema = schemas[binding["schema"]]
        require(schema.get("type") == "object" and not any(k in schema for k in ("$ref", "allOf", "anyOf", "oneOf")),
                "ARCHITECTURE_UNSUPPORTED_NAME_BINDING")
        properties = schema.get("properties", {})
        require(properties.keys() == binding["fields"].keys() | binding["derived_fields"].keys(),
                "ARCHITECTURE_FIELD_NAME_MISMATCH",
                "Архитектор: поля DTO " + binding["schema"] + " отличаются от утверждённой карты имён.")
        entity = entities[binding["entity"]]
        fields = {field["name"]: field for field in entity["fields"]}
        for api_name, data_name in binding["fields"].items():
            require(data_name in fields, "ARCHITECTURE_FIELD_NAME_MISMATCH",
                    "Архитектор: поле " + binding["schema"] + "." + api_name + " не соответствует "
                    + binding["entity"] + "." + data_name + ". Автоматическое переименование запрещено.")
            api, field = properties[api_name], fields[data_name]
            require(not any(k in api for k in ("$ref", "allOf", "anyOf", "oneOf")),
                    "ARCHITECTURE_UNSUPPORTED_NAME_BINDING")
            api_types = api.get("type", [])
            api_types = [api_types] if isinstance(api_types, str) else api_types
            expected_type, expected_format = kinds[field["type"]]
            require(set(api_types) - {"null"} == {expected_type}
                    and (expected_format is None or api.get("format") == expected_format)
                    and ("null" not in api_types or field["nullable"]), "ARCHITECTURE_FIELD_TYPE_MISMATCH",
                    "Архитектор: несовместимые типы API/data для " + binding["schema"] + "." + api_name + ".")
            if binding["schema"] in create_schemas and not field["nullable"] and field["default"]["kind"] == "none":
                require(api_name in schema.get("required", []), "ARCHITECTURE_FIELD_REQUIRED_MISMATCH")
            if field["type"] == "varchar":
                require("maxLength" in api and api["maxLength"] <= field["max_length"], "ARCHITECTURE_FIELD_CONSTRAINT_MISMATCH")
            for check in entity["check_constraints"]:
                if check["field"] == data_name and check["operator"] == "in":
                    require("enum" in api and set(api["enum"]) == set(check["value"]),
                            "ARCHITECTURE_FIELD_CONSTRAINT_MISMATCH")
