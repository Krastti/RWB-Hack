"""UI schema and reference validation."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource

from contracts.agents.validate import load, walk

from .bindings import validate_bindings
from .errors import UiSpecError, require

ROOT = Path(__file__).resolve().parent
DRAFT_FIELDS = ("flows", "screens", "non_ui_requirements")


def _no_remote(uri: str):
    raise NoSuchResource(ref=uri)


def ui_spec_schema() -> dict:
    return load(ROOT / "ui-spec.schema.json")


def draft_schema() -> dict:
    schema = ui_spec_schema()
    return {"$schema": schema["$schema"], "type": "object",
            "properties": {key: schema["properties"][key] for key in DRAFT_FIELDS},
            "required": list(DRAFT_FIELDS), "additionalProperties": False, "$defs": schema["$defs"]}


def validate_shape(value: dict, schema: dict) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        validator = Draft202012Validator(schema, format_checker=FormatChecker(),
                                         registry=Registry(retrieve=_no_remote))
        require(not any(validator.iter_errors(value)), "UI_INVALID_SPEC")
    except UiSpecError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise UiSpecError("UI_INVALID_SPEC") from None


def _index(items: list[dict], code: str) -> dict[str, dict]:
    result = {item["id"]: item for item in items}
    require(len(result) == len(items), code)
    return result


def _access(access: dict, roles: set[str]) -> None:
    require(set(access["role_ids"]) <= roles, "UI_UNKNOWN_ROLE")
    require((access["mode"] == "public") == (not access["role_ids"]), "UI_INVALID_ACCESS")


def _within(child: dict, parent: dict) -> bool:
    return (parent["mode"] == "public"
            or child["mode"] == "roles" and set(child["role_ids"]) <= set(parent["role_ids"]))


def _security_key(security: list[dict]) -> frozenset:
    alternatives = []
    for alternative in security:
        schemes = alternative["schemes"]
        require(len({scheme["scheme_id"] for scheme in schemes}) == len(schemes),
                "UI_SECURITY_MISMATCH")
        alternatives.append(frozenset((scheme["scheme_id"], frozenset(scheme["scopes"])) for scheme in schemes))
    require(len(set(alternatives)) == len(alternatives), "UI_SECURITY_MISMATCH")
    return frozenset(alternatives)


def validate_ui_spec(spec: dict, *, metadata: dict, requirements: dict,
                     operations: dict, profile: dict, openapi: dict) -> None:
    validate_shape(spec, ui_spec_schema())
    require({key: value for key, value in spec.items() if key not in DRAFT_FIELDS} == metadata,
            "UI_SOURCE_MISMATCH")
    frs = {item["id"] for item in requirements["functional_requirements"]}
    roles = {item["id"] for item in requirements["roles"]}
    for node in walk(spec):
        if isinstance(node, dict):
            if "related_fr_ids" in node:
                require(set(node["related_fr_ids"]) <= frs, "UI_UNKNOWN_FR")
            if "access" in node:
                _access(node["access"], roles)
    screens = _index(spec["screens"], "UI_DUPLICATE_SCREEN")
    _index(spec["flows"], "UI_DUPLICATE_FLOW")
    all_components, all_actions, routes = set(), set(), set()
    actions_by_screen = {}
    covered = set()
    widths = set(profile["ui_kit"]["responsive_widths_px"])
    states = set(profile["ui_kit"]["required_states"])
    for screen in screens.values():
        route = tuple(part for part in screen["route"].split("/") if part)
        require(not any(len(route) == len(previous) and all(
            left == right or left.startswith(":") or right.startswith(":")
            for left, right in zip(route, previous)) for previous in routes), "UI_DUPLICATE_ROUTE")
        routes.add(route)
        components = _index(screen["components"], "UI_DUPLICATE_COMPONENT")
        actions = _index(screen["actions"], "UI_DUPLICATE_ACTION")
        require(not (all_components & components.keys()), "UI_DUPLICATE_COMPONENT")
        require(not (all_actions & actions.keys()), "UI_DUPLICATE_ACTION")
        all_components.update(components)
        all_actions.update(actions)
        actions_by_screen[screen["id"]] = actions
        screen_frs = set(screen["related_fr_ids"])
        covered.update(screen_frs)
        for component in components.values():
            require(set(component["related_fr_ids"]) <= screen_frs, "UI_COMPONENT_FR_MISMATCH")
        implemented_frs = {fr for item in [*components.values(), *actions.values()] for fr in item["related_fr_ids"]}
        require(screen_frs <= implemented_frs, "UI_SCREEN_FR_UNCOVERED")
        for action in actions.values():
            require(set(action["related_fr_ids"]) <= screen_frs, "UI_ACTION_FR_MISMATCH")
            require(_within(action["access"], screen["access"]), "UI_ACTION_ACCESS_WIDENED")
            require(action["component_id"] in components or action["component_id"] is None
                    and action["trigger"] == "on_enter", "UI_UNKNOWN_COMPONENT")
            target = action["target_screen_id"]
            require(target is None or target in screens, "UI_UNKNOWN_SCREEN")
            if target is not None:
                require(_within(action["access"], screens[target]["access"]), "UI_TARGET_ACCESS_MISMATCH")
            if action["kind"] == "api":
                require(action["operation_id"] in operations, "UI_UNKNOWN_OPERATION")
                indexed = operations[action["operation_id"]]
                operation = indexed["operation"]
                require(_security_key(action["security"]) == _security_key(indexed["security"]),
                        "UI_SECURITY_MISMATCH")
                if indexed["security"] and all(item["schemes"] for item in indexed["security"]):
                    require(action["access"]["mode"] == "roles", "UI_OPERATION_AUTH_REQUIRED")
                if "x-role-ids" in operation:
                    require(action["access"]["mode"] == "roles"
                            and set(action["access"]["role_ids"]) <= set(operation["x-role-ids"]),
                            "UI_OPERATION_ACCESS_WIDENED")
                if "x-fr-ids" in operation:
                    require(set(action["related_fr_ids"]) <= set(operation["x-fr-ids"]),
                            "UI_OPERATION_FR_MISMATCH")
                validate_bindings(action, components, indexed, openapi)
            else:
                require(action["operation_id"] is None, "UI_INVALID_ACTION")
                require((action["kind"] == "navigate") == (target is not None), "UI_INVALID_ACTION")
                require(not action["security"] and not action["request_bindings"]
                        and not action["response_bindings"], "UI_INVALID_ACTION")
        require(set(screen["states"]) == states, "UI_MISSING_STATE")
        for state in screen["states"].values():
            require(set(state["component_ids"]) <= components.keys(), "UI_UNKNOWN_COMPONENT")
            require(set(state["action_ids"]) <= actions.keys(), "UI_UNKNOWN_ACTION")
        require({layout["width_px"] for layout in screen["responsive"]} == widths
                and len(screen["responsive"]) == len(widths), "UI_RESPONSIVE_WIDTHS")
        if any(layout["overflow"] == "table_scroll" for layout in screen["responsive"]):
            require(any(item["catalog_id"] == "table" for item in components.values()), "UI_TABLE_SCROLL_WITHOUT_TABLE")
    non_ui = spec["non_ui_requirements"]
    excluded = {item["fr_id"] for item in non_ui}
    require(len(excluded) == len(non_ui) and not (excluded & covered), "UI_INVALID_COVERAGE")
    require(covered | excluded == frs, "UI_INCOMPLETE_FR_COVERAGE")
    visited = set()
    for flow in spec["flows"]:
        steps = flow["steps"]
        require(flow["entry_screen_id"] == steps[0]["screen_id"], "UI_FLOW_ENTRY_MISMATCH")
        flow_frs = set()
        for index, step in enumerate(steps):
            screen = screens.get(step["screen_id"])
            require(screen is not None, "UI_UNKNOWN_SCREEN")
            action = actions_by_screen[screen["id"]].get(step["action_id"])
            require(action is not None, "UI_UNKNOWN_ACTION")
            require(_within(flow["access"], screen["access"]) and _within(flow["access"], action["access"]),
                    "UI_FLOW_ACCESS_MISMATCH")
            flow_frs.update(screen["related_fr_ids"])
            visited.add(screen["id"])
            target = action["target_screen_id"] or screen["id"]
            visited.add(target)
            if index + 1 < len(steps):
                require(steps[index + 1]["screen_id"] == target, "UI_BROKEN_FLOW")
        require(set(flow["related_fr_ids"]) <= flow_frs, "UI_FLOW_FR_MISMATCH")
    require(visited == screens.keys(), "UI_UNREACHABLE_SCREEN")
