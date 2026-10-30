"""Validate data-model structure, references and PostgreSQL constraints."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
import json
import operator
from pathlib import Path
import re
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from contracts.agents.validate import ContractError, Contracts, load, walk


class DataModelError(ContractError):
    """A safe diagnostic code with optional structured blockers."""

    def __init__(self, code: str, *, unresolved_items: list[dict] | None = None) -> None:
        super().__init__(code)
        self.unresolved_items = deepcopy(unresolved_items or [])


def require(condition: bool, code: str) -> None:
    if not condition:
        raise DataModelError(code)


@lru_cache(maxsize=1)
def _contracts() -> Contracts:
    return Contracts()


@lru_cache(maxsize=1)
def _schema() -> dict:
    schema = load(Path(__file__).with_name("model.schema.json"))
    Draft202012Validator.check_schema(schema)
    return schema


def data_model_schema() -> dict:
    """Return the response schema with local references."""
    return deepcopy(_schema())


def _unique(items: list[dict], key: str, code: str) -> None:
    values = [item[key] for item in items]
    require(len(values) == len(set(values)), code)


def _literal(value: object, field: dict) -> bool:
    if value is None:
        return field["nullable"]
    kind = field["type"]
    if kind in {"varchar", "text"}:
        return isinstance(value, str) and (kind == "text" or len(value) <= field["max_length"])
    if kind in {"integer", "bigint"}:
        bits = 32 if kind == "integer" else 64
        return type(value) is int and -(2 ** (bits - 1)) <= value < 2 ** (bits - 1)
    if kind == "boolean":
        return type(value) is bool
    if kind == "numeric":
        if not isinstance(value, str) or re.fullmatch(r"-?\d+(?:\.\d+)?", value) is None:
            return False
        number = Decimal(value)
        _, digits, exponent = number.as_tuple()
        digits = list(digits)
        while digits and digits[-1] == 0:
            digits.pop()
            exponent += 1
        if not digits:
            return True
        return (max(0, -exponent) <= field["scale"] and
                max(0, len(digits) + exponent) <= field["precision"] - field["scale"])
    if kind == "uuid":
        try:
            return isinstance(value, str) and str(UUID(value)) == value
        except (ValueError, AttributeError):
            return False
    if kind == "date":
        try:
            return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
        except ValueError:
            return False
    if kind == "timestamptz":
        try:
            return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() is not None
        except ValueError:
            return False
    return False


def _field(field: dict) -> None:
    kind = field["type"]
    require((field["max_length"] is not None) == (kind == "varchar"), "DATA_MODEL_LENGTH_INVALID")
    require((field["precision"] is not None) == (kind == "numeric") and
            (field["scale"] is not None) == (kind == "numeric"), "DATA_MODEL_NUMERIC_INVALID")
    if kind == "numeric":
        require(field["scale"] <= field["precision"], "DATA_MODEL_NUMERIC_INVALID")
    default = field["default"]
    if default["kind"] == "literal":
        require(kind != "jsonb" and _literal(default["value"], field), "DATA_MODEL_DEFAULT_INVALID")
    else:
        require(default["value"] is None, "DATA_MODEL_DEFAULT_INVALID")
        require(default["kind"] != "application_uuid4" or kind == "uuid", "DATA_MODEL_DEFAULT_INVALID")
        require(default["kind"] != "server_now" or kind == "timestamptz", "DATA_MODEL_DEFAULT_INVALID")


def _comparable(value: object, field: dict) -> object:
    if field["type"] == "numeric":
        return Decimal(value)
    if field["type"] == "date":
        return date.fromisoformat(value)
    if field["type"] == "timestamptz":
        return datetime.fromisoformat(value)
    return value


def _accepts_default(check: dict, field: dict) -> bool:
    value = field["default"]["value"]
    operation = check["operator"]
    if operation == "is_null":
        return value is None
    if operation == "is_not_null":
        return value is not None
    if value is None:
        return True
    left = _comparable(value, field)
    if operation in {"in", "not_in"}:
        present = left in [_comparable(item, field) for item in check["value"]]
        return present if operation == "in" else not present
    comparisons = {
        "eq": operator.eq, "ne": operator.ne, "lt": operator.lt,
        "lte": operator.le, "gt": operator.gt, "gte": operator.ge,
    }
    return comparisons[operation](left, _comparable(check["value"], field))


def _check(check: dict, fields: dict) -> None:
    require(check["field"] in fields, "DATA_MODEL_UNKNOWN_FIELD")
    field = fields[check["field"]]
    operation = check["operator"]
    value = check["value"]
    if operation in {"is_null", "is_not_null"}:
        require(value is None, "DATA_MODEL_CHECK_INVALID")
        require(operation != "is_null" or field["nullable"], "DATA_MODEL_CHECK_CONTRADICTION")
    else:
        if operation in {"lt", "lte", "gt", "gte"}:
            require(field["type"] in {"integer", "bigint", "numeric", "date", "timestamptz"},
                    "DATA_MODEL_CHECK_INVALID")
        values = value if operation in {"in", "not_in"} else [value]
        require(isinstance(values, list) and bool(values) and
                all(item is not None and _literal(item, field) for item in values),
                "DATA_MODEL_CHECK_INVALID")
        require(operation in {"in", "not_in"} or not isinstance(value, list), "DATA_MODEL_CHECK_INVALID")
    if field["default"]["kind"] == "literal":
        require(_accepts_default(check, field), "DATA_MODEL_DEFAULT_VIOLATES_CHECK")


def validate_data_model(model: dict, *, requirements: dict,
                        requirements_artifact_id: str, contract_version: str,
                        profile_id: str = "web-fastapi-angular") -> None:
    """Validate a model against its accepted requirements and contract version."""
    try:
        json.dumps(model, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError):
        raise DataModelError("DATA_MODEL_INVALID_JSON") from None
    validator = Draft202012Validator(_schema(), format_checker=FormatChecker())
    require(not any(validator.iter_errors(model)), "DATA_MODEL_INVALID_SCHEMA")
    _contracts().validate("requirements", requirements)
    require(model["requirements_artifact_id"] == requirements_artifact_id and
            model["contract_version"] == contract_version and model["profile_id"] == profile_id,
            "DATA_MODEL_CONTEXT_MISMATCH")
    require(not any(q["required"] and "answer" not in q for q in requirements["questions"]),
            "DATA_MODEL_REQUIRES_ANSWERS")
    frs = {item["id"] for item in requirements["functional_requirements"]}
    for item in walk(model):
        if isinstance(item, dict) and "related_fr_ids" in item:
            require(set(item["related_fr_ids"]) <= frs, "DATA_MODEL_UNKNOWN_FR")
    if model["unresolved_items"]:
        raise DataModelError("DATA_MODEL_UNRESOLVED", unresolved_items=model["unresolved_items"])
    _unique(model["entities"], "name", "DATA_MODEL_DUPLICATE_ENTITY")
    _unique(model["relationships"], "name", "DATA_MODEL_DUPLICATE_RELATIONSHIP")
    entities = {item["name"]: item for item in model["entities"]}
    fields_by_entity, keys_by_entity = {}, {}
    relation_names = set(entities)
    constraint_names = {}
    for entity in model["entities"]:
        for source in entity["source_refs"]:
            require(any(item["text"] == source["text"] for item in requirements[source["section"]]),
                    "DATA_MODEL_UNKNOWN_SOURCE")
        _unique(entity["fields"], "name", "DATA_MODEL_DUPLICATE_FIELD")
        fields = {item["name"]: item for item in entity["fields"]}
        fields_by_entity[entity["name"]] = fields
        for field in fields.values():
            require(set(field["related_fr_ids"]) <= set(entity["related_fr_ids"]),
                    "DATA_MODEL_FIELD_FR_MISMATCH")
            _field(field)
        keys = [entity["primary_key"], *entity["unique_constraints"]]
        names = [item["name"] for item in [*keys, *entity["check_constraints"], *entity["indexes"]]]
        require(len(names) == len(set(names)), "DATA_MODEL_DUPLICATE_CONSTRAINT")
        constraint_names[entity["name"]] = set(names)
        for key in [*keys, *entity["indexes"]]:
            require(set(key["fields"]) <= fields.keys(), "DATA_MODEL_UNKNOWN_FIELD")
        require(all(not fields[name]["nullable"] for name in entity["primary_key"]["fields"]),
                "DATA_MODEL_NULLABLE_PRIMARY_KEY")
        keys_by_entity[entity["name"]] = {
            tuple(key["fields"]) for key in [*keys, *(i for i in entity["indexes"] if i["unique"])]
        }
        for key in [*keys, *entity["indexes"]]:
            require(key["name"] not in relation_names, "DATA_MODEL_RELATION_NAME_CONFLICT")
            relation_names.add(key["name"])
        for check in entity["check_constraints"]:
            _check(check, fields)
    for relation in model["relationships"]:
        source, target = relation["source_entity"], relation["target_entity"]
        require(source in entities and target in entities, "DATA_MODEL_UNKNOWN_ENTITY")
        require(relation["name"] not in constraint_names[source], "DATA_MODEL_DUPLICATE_CONSTRAINT")
        constraint_names[source].add(relation["name"])
        sf, tf = relation["source_fields"], relation["target_fields"]
        require(len(sf) == len(tf) and set(sf) <= fields_by_entity[source].keys() and
                set(tf) <= fields_by_entity[target].keys(), "DATA_MODEL_FOREIGN_KEY_INVALID")
        require(tuple(tf) in keys_by_entity[target], "DATA_MODEL_FOREIGN_KEY_NOT_UNIQUE")
        for left, right in zip(sf, tf):
            require(all(fields_by_entity[source][left][k] == fields_by_entity[target][right][k]
                        for k in ("type", "max_length", "precision", "scale")),
                    "DATA_MODEL_FOREIGN_KEY_TYPE_MISMATCH")
        if relation["on_delete"] == "set_null":
            require(all(fields_by_entity[source][name]["nullable"] for name in sf),
                    "DATA_MODEL_SET_NULL_INVALID")
        if relation["cardinality"] == "one_to_one":
            require(tuple(sf) in keys_by_entity[source], "DATA_MODEL_CARDINALITY_INVALID")
    _unique(model["fr_coverage"], "fr_id", "DATA_MODEL_DUPLICATE_COVERAGE")
    require({item["fr_id"] for item in model["fr_coverage"]} == frs, "DATA_MODEL_INCOMPLETE_FR_COVERAGE")
    for coverage in model["fr_coverage"]:
        require(set(coverage["entity_names"]) <= entities.keys(), "DATA_MODEL_UNKNOWN_ENTITY")
        actual = {name for name, entity in entities.items() if coverage["fr_id"] in entity["related_fr_ids"]}
        for relation in model["relationships"]:
            if coverage["fr_id"] in relation["related_fr_ids"]:
                actual.update((relation["source_entity"], relation["target_entity"]))
        require(set(coverage["entity_names"]) == actual, "DATA_MODEL_COVERAGE_MISMATCH")
        if coverage["status"] == "modeled":
            require(bool(actual), "DATA_MODEL_COVERAGE_MISMATCH")
        else:
            require(not coverage["entity_names"], "DATA_MODEL_COVERAGE_MISMATCH")
