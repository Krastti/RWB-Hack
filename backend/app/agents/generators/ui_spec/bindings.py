"""Validate UI data bindings against the selected API operation."""

from .errors import require
from .openapi import resolve_object


def _schema(document: dict, value: dict, direction: str) -> dict:
    value = resolve_object(document, value)
    require(not any(key in value for key in (
        "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "dependentSchemas", "dependentRequired",
        "$dynamicRef", "prefixItems", "contains",
    )),
            "UI_BINDING_UNSUPPORTED_SCHEMA")
    flag = "readOnly" if direction == "request" else "writeOnly"
    require(not value.get(flag, False), "UI_BINDING_DIRECTION")
    require(isinstance(value.get("properties", {}), dict)
            and isinstance(value.get("required", []), list)
            and all(isinstance(name, str) for name in value.get("required", [])),
            "UI_BINDING_UNSUPPORTED_SCHEMA")
    return value


def _field(document: dict, schema: dict, path: list[str], direction: str) -> None:
    value = _schema(document, schema, direction)
    for part in path:
        if part == "*":
            require(value.get("type") == "array" and "items" in value, "UI_UNKNOWN_API_FIELD")
            value = value["items"]
        else:
            properties = value.get("properties", {})
            require(isinstance(properties, dict) and part in properties, "UI_UNKNOWN_API_FIELD")
            value = properties[part]
        value = _schema(document, value, direction)


def _component(binding: dict, components: dict) -> None:
    component = components.get(binding["component_id"])
    require(component is not None, "UI_UNKNOWN_COMPONENT")
    require(binding["component_field"] in component["data_fields"], "UI_UNKNOWN_COMPONENT_FIELD")


def _content_schema(value: dict, media_type: str) -> dict:
    content = value.get("content", {})
    require(isinstance(content, dict) and media_type in content, "UI_UNKNOWN_MEDIA_TYPE")
    media = content[media_type]
    require(isinstance(media, dict) and isinstance(media.get("schema"), dict), "UI_BINDING_UNSUPPORTED_SCHEMA")
    return media["schema"]


def _parameter_schema(parameter: dict) -> dict:
    require(isinstance(parameter.get("schema"), dict), "UI_BINDING_UNSUPPORTED_SCHEMA")
    return parameter["schema"]


def _required(document: dict, schema: dict, paths: set[tuple], prefix: tuple = ()) -> None:
    value = _schema(document, schema, "request")
    if any(prefix[:len(path)] == path for path in paths):
        return
    require(len(prefix) <= 64 and any(path[:len(prefix)] == prefix for path in paths),
            "UI_MISSING_REQUEST_BINDING")
    properties = value.get("properties", {})
    require(set(value.get("required", [])) <= properties.keys(), "UI_BINDING_UNSUPPORTED_SCHEMA")
    for name, schema in properties.items():
        child_path = prefix + (name,)
        if name in value.get("required", []) or any(path[:len(child_path)] == child_path for path in paths):
            child = resolve_object(document, schema)
            if not child.get("readOnly", False):
                _required(document, child, paths, child_path)
    if value.get("type") == "array" and "items" in value:
        _required(document, value["items"], paths, prefix + ("*",))


def validate_bindings(action: dict, components: dict, indexed: dict, document: dict) -> None:
    operation = indexed["operation"]
    parameters = {(item["in"], item["name"]): item for item in indexed["parameters"]}
    body = resolve_object(document, operation["requestBody"]) if "requestBody" in operation else None
    destinations = {}
    for binding in action["request_bindings"]:
        _component(binding, components)
        location, name, media_type = (binding[key] for key in ("location", "parameter_name", "media_type"))
        if location == "body":
            require(name is None and media_type is not None and body is not None, "UI_INVALID_REQUEST_BINDING")
            schema = _content_schema(body, media_type)
        else:
            require(media_type is None and (location, name) in parameters, "UI_UNKNOWN_PARAMETER")
            schema = _parameter_schema(parameters[location, name])
        _field(document, schema, binding["field_path"], "request")
        key = (location, name, media_type)
        path = tuple(binding["field_path"])
        paths = destinations.setdefault(key, set())
        require(not any(path[:len(previous)] == previous or previous[:len(path)] == path for previous in paths),
                "UI_DUPLICATE_REQUEST_BINDING")
        paths.add(path)
    body_targets = [key for key in destinations if key[0] == "body"]
    require(len(body_targets) <= 1, "UI_MULTIPLE_REQUEST_MEDIA_TYPES")
    for (location, name), parameter in parameters.items():
        key = (location, name, None)
        if parameter.get("required", False) or key in destinations:
            require(key in destinations, "UI_MISSING_REQUEST_BINDING")
            _required(document, _parameter_schema(parameter), destinations[key])
    if body is not None:
        require(not body.get("required", False) or body_targets, "UI_MISSING_REQUEST_BINDING")
        for key in body_targets:
            _required(document, _content_schema(body, key[2]), destinations[key])

    responses = {status: resolve_object(document, response) for status, response in operation["responses"].items()
                 if not status.startswith("x-")}
    bound_responses, targets = set(), set()
    for binding in action["response_bindings"]:
        _component(binding, components)
        status, media_type = binding["status_code"], binding["media_type"]
        require(status in responses and status.startswith("2"), "UI_UNKNOWN_RESPONSE")
        schema = _content_schema(responses[status], media_type)
        _field(document, schema, binding["field_path"], "response")
        target = (status, binding["component_id"], binding["component_field"])
        require(target not in targets, "UI_DUPLICATE_RESPONSE_BINDING")
        targets.add(target)
        bound_responses.add(status)
    for status, response in responses.items():
        if status.startswith("2") and response.get("content"):
            require(status in bound_responses, "UI_MISSING_RESPONSE_BINDING")
