"""Read OpenAPI operations and resolve local references."""

from __future__ import annotations

import re
from urllib.parse import unquote

import yaml

from contracts.agents.validate import loads, walk

from .validation import UiSpecError, require

METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            require(isinstance(key, str) and key not in result, "UI_OPENAPI_INVALID_YAML")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_openapi(content: str, media_type: str) -> dict:
    try:
        if media_type == "application/json":
            document = loads(content)
        else:
            require(media_type in {"application/yaml", "application/x-yaml", "text/yaml"},
                    "UI_OPENAPI_MEDIA_TYPE")
            require(not any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(content)),
                    "UI_OPENAPI_YAML_ALIAS")
            document = yaml.load(content, Loader=_UniqueLoader)
        require(isinstance(document, dict), "UI_OPENAPI_INVALID")
        return document
    except UiSpecError:
        raise
    except (ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise UiSpecError("UI_OPENAPI_INVALID") from None


def _pointer(document: dict, reference: str):
    require(isinstance(reference, str) and reference.startswith("#/"), "UI_OPENAPI_EXTERNAL_REF")
    pointer = unquote(reference[2:])
    current = document
    for part in pointer.split("/"):
        require(re.search("~(?![01])", part) is None, "UI_OPENAPI_UNRESOLVED_REF")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            require(key in current, "UI_OPENAPI_UNRESOLVED_REF")
            current = current[key]
        elif isinstance(current, list) and key.isdigit():
            require(int(key) < len(current), "UI_OPENAPI_UNRESOLVED_REF")
            current = current[int(key)]
        else:
            raise UiSpecError("UI_OPENAPI_UNRESOLVED_REF")
    return current


def _path_item(document: dict, item: dict) -> dict:
    seen = set()
    while "$ref" in item:
        reference = item["$ref"]
        # OpenAPI leaves conflicting Path Item siblings undefined.
        require(len(item) == 1 and reference not in seen, "UI_OPENAPI_PATH_REF")
        seen.add(reference)
        item = _pointer(document, reference)
        require(isinstance(item, dict), "UI_OPENAPI_PATH_REF")
    return item


def operation_index(document: dict, *, contract_version: str, fr_ids: set[str],
                    role_ids: set[str]) -> dict[str, dict]:
    require(document.get("openapi") == "3.1.0", "UI_OPENAPI_VERSION")
    require(isinstance(document.get("info"), dict)
            and document["info"].get("version") == contract_version, "UI_CONTRACT_VERSION_MISMATCH")
    require(isinstance(document.get("paths"), dict), "UI_OPENAPI_INVALID")
    # Check references without expanding recursive schemas.
    for node in walk(document):
        if isinstance(node, dict) and "$ref" in node:
            _pointer(document, node["$ref"])
    result = {}
    for path, raw_item in document["paths"].items():
        if path.startswith("x-"):
            continue
        require(path.startswith("/") and isinstance(raw_item, dict), "UI_OPENAPI_INVALID_PATH")
        item = _path_item(document, raw_item)
        for method, operation in item.items():
            if method not in METHODS:
                continue
            require(isinstance(operation, dict), "UI_OPENAPI_INVALID_OPERATION")
            operation_id = operation.get("operationId")
            require(isinstance(operation_id, str) and bool(operation_id.strip()), "UI_OPENAPI_MISSING_OPERATION_ID")
            require(operation_id not in result, "UI_OPENAPI_DUPLICATE_OPERATION_ID")
            require(isinstance(operation.get("responses"), dict) and bool(operation["responses"]),
                    "UI_OPENAPI_RESPONSES_REQUIRED")
            for key, allowed in [("x-fr-ids", fr_ids), ("x-role-ids", role_ids)]:
                if key in operation:
                    values = operation[key]
                    require(isinstance(values, list) and bool(values)
                            and all(isinstance(value, str) for value in values)
                            and len(set(values)) == len(values) and set(values) <= allowed,
                            "UI_OPENAPI_UNKNOWN_BINDING")
            result[operation_id] = {"method": method, "path": path, "operation": operation}
    return result
