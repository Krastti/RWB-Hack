"""UI specification checks with the real adapter and offline transport."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import unittest

import httpx
import yaml
from jsonschema import Draft202012Validator

from contracts.agents.validate import Contracts
from backend.app.agents.generators.ui_spec import (
    UiSpecError, build_agent_result, generate_ui_spec, prepare_ui_spec, ui_spec_schema, validate_ui_spec,
)
from backend.app.agents.generators.ui_spec.example import example_inputs, identifier
from backend.app.agents.generators.ui_spec.generator import ROOT
from backend.app.agents.generators.ui_spec.validation import draft_schema
from backend.app.agents.llm import LLMAdapter, LLMConfig


def draft():
    return json.loads((ROOT / "examples/draft.json").read_text(encoding="utf-8"))


def update_artifact(inputs, key, update=None, *, content=None, media_type=None):
    artifact = inputs[key]
    if content is None:
        value = json.loads(artifact["content"])
        update(value)
        content = json.dumps(value, ensure_ascii=False)
    artifact["content"] = content
    artifact["checksum"] = sha256(content.encode()).hexdigest()
    artifact["size_bytes"] = len(content.encode())
    if media_type:
        artifact["media_type"] = media_type


class UiSpecTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.inputs = example_inputs()
        self.prepared = prepare_ui_spec(**self.inputs)

    async def generate(self, payload=None, *, prepared=None, status=200, usage=True, content=None):
        prepared = prepared or self.prepared
        self.requests = []

        def respond(request):
            self.requests.append(request)
            body = {"choices": [{"finish_reason": "stop", "message": {
                "content": content if content is not None else json.dumps(payload if payload is not None else draft()),
            }}]}
            if usage:
                body["usage"] = {"prompt_tokens": 17, "completion_tokens": 29, "total_tokens": 46}
            return httpx.Response(status, json=body)

        adapter = LLMAdapter(LLMConfig(
            base_url="https://example.invalid/v1", model="offline", api_key="test-placeholder",
            max_output_tokens=prepared.max_output_tokens, timeout_seconds=prepared.timeout_seconds,
        ), transport=httpx.MockTransport(respond))
        return await generate_ui_spec(adapter, prepared)

    def pack(self, result, prepared=None, **overrides):
        arguments = {"attempt_id": identifier("attempt"), "artifact_id": identifier("artifact"),
                     "artifact_version": 1, "call_id": identifier("call"), "model": "offline",
                     "duration_seconds": 0.1}
        arguments.update(overrides)
        return build_agent_result(prepared or self.prepared, result, **arguments)

    def prepare_openapi(self, update):
        inputs = example_inputs()
        update_artifact(inputs, "openapi_artifact", update)
        return prepare_ui_spec(**inputs)

    async def assert_rejected(self, value, code, *, prepared=None):
        result = await self.generate(value, prepared=prepared)
        self.assertFalse(result.ok)
        self.assertEqual(result.error.code, code)
        self.assertEqual(result.usage.calls, 1)
        envelope = self.pack(result, prepared)
        self.assertEqual(envelope["output_artifacts"], [])
        self.assertEqual(envelope["usage"]["output_tokens"], 29)

    async def test_adapter_and_c02_result(self):
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        self.assertEqual(len(self.requests), 1)
        request = json.loads(self.requests[0].content)
        self.assertEqual(request["max_tokens"], self.prepared.max_output_tokens)
        self.assertEqual(set(request["response_format"]["json_schema"]["schema"]["properties"]),
                         {"flows", "screens", "non_ui_requirements"})
        envelope = self.pack(result)
        Contracts().validate("agent-result", envelope)
        self.assertEqual(envelope["status"], "succeeded")
        artifact = envelope["output_artifacts"][0]
        self.assertEqual(artifact["path"], "ui-spec.json")
        self.assertEqual(artifact["author_agent"], "architecture")
        self.assertEqual(artifact["size_bytes"], len(artifact["content"].encode()))
        self.assertEqual(artifact["checksum"], sha256(artifact["content"].encode()).hexdigest())
        self.assertEqual(envelope["stage_id"], "architecture-ui")

    async def test_unknown_tokens_stay_null(self):
        result = await self.generate(usage=False)
        self.assertTrue(result.ok, result.error)
        envelope = self.pack(result)
        self.assertIsNone(envelope["usage"]["input_tokens"])
        self.assertIsNone(envelope["usage"]["output_tokens"])

    async def test_provider_error_has_one_call_no_output(self):
        result = await self.generate(status=503)
        self.assertEqual(result.error.code, "LLM_UNAVAILABLE")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.pack(result)["status"], "failed")
        self.assertEqual(self.pack(result)["output_artifacts"], [])

    async def test_malformed_response_keeps_usage(self):
        result = await self.generate(content='{"private":')
        self.assertEqual(result.error.code, "LLM_INVALID_RESPONSE")
        self.assertEqual(self.pack(result)["usage"]["output_tokens"], 29)

    async def test_cancel_propagates(self):
        async def respond(request):
            raise asyncio.CancelledError()

        adapter = LLMAdapter(LLMConfig(base_url="https://example.invalid", model="offline", api_key="test"),
                             transport=httpx.MockTransport(respond))
        with self.assertRaises(asyncio.CancelledError):
            await generate_ui_spec(adapter, self.prepared)

    async def test_output_guards(self):
        cases = [
            ("UI_UNKNOWN_FR", lambda d: d["screens"][0]["related_fr_ids"].append("FR-999")),
            ("UI_UNKNOWN_ROLE", lambda d: d["screens"][0]["access"]["role_ids"].append("ROLE-999")),
            ("UI_INVALID_ACCESS", lambda d: d["screens"][0]["access"].update(role_ids=[])),
            ("UI_UNKNOWN_COMPONENT", lambda d: d["screens"][0]["actions"][0].update(component_id="COMP-999")),
            ("UI_UNKNOWN_ACTION", lambda d: d["screens"][0]["states"]["error"].update(action_ids=["ACT-999"])),
            ("UI_UNKNOWN_SCREEN", lambda d: d["screens"][0]["actions"][0].update(target_screen_id="SCREEN-999")),
            ("UI_UNKNOWN_OPERATION", lambda d: d["screens"][0]["actions"][0].update(operation_id="invented")),
            ("LLM_INVALID_RESPONSE", lambda d: d["screens"][0]["components"][0].update(catalog_id="MuiDataGrid")),
            ("LLM_INVALID_RESPONSE", lambda d: d["screens"][0]["states"].pop("error")),
            ("UI_RESPONSIVE_WIDTHS", lambda d: d["screens"][0]["responsive"][0].update(width_px=768)),
            ("UI_DUPLICATE_SCREEN", lambda d: d["screens"][1].update(id="SCREEN-001")),
            ("UI_DUPLICATE_ACTION", lambda d: d["screens"][1]["actions"][0].update(id="ACT-001")),
            ("UI_DUPLICATE_COMPONENT", lambda d: d["screens"][1]["components"][0].update(id="COMP-001")),
            ("UI_DUPLICATE_FLOW", lambda d: d["flows"][1].update(id="FLOW-001")),
            ("UI_FLOW_ENTRY_MISMATCH", lambda d: d["flows"][0].update(entry_screen_id="SCREEN-002")),
            ("UI_UNREACHABLE_SCREEN", lambda d: d["flows"].pop()),
            ("UI_BROKEN_FLOW", lambda d: d["flows"][0]["steps"][1].update(screen_id="SCREEN-002", action_id="ACT-003")),
            ("UI_ACTION_ACCESS_WIDENED", lambda d: d["screens"][0]["actions"][0]["access"].update(mode="public", role_ids=[])),
            ("UI_INVALID_ACTION", lambda d: d["screens"][0]["actions"][0].update(kind="local")),
            ("LLM_INVALID_RESPONSE", lambda d: d.update(contract_version="999")),
            ("UI_INVALID_COVERAGE", lambda d: d["non_ui_requirements"].append({"fr_id": "FR-001", "reason": "No UI"})),
        ]
        for code, change in cases:
            with self.subTest(code=code):
                value = draft()
                change(value)
                await self.assert_rejected(value, code)

    async def test_profile_and_input_are_frozen(self):
        original = self.prepared.inputs_json
        self.inputs["context"]["contract_version"] = "999"
        self.inputs["requirements_artifact"]["content"] = "changed"
        self.inputs["profile_content"] = "{}"
        self.assertEqual(self.prepared.inputs_json, original)
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.data["contract_version"], "1")
        self.assertEqual(result.data["profile_checksum"], sha256(example_inputs()["profile_content"].encode()).hexdigest())
        result.data["sources"]["openapi"]["checksum"] = "0" * 64
        with self.assertRaisesRegex(UiSpecError, "UI_SOURCE_MISMATCH"):
            validate_ui_spec(result.data, self.prepared)

    async def test_artifact_limit_and_invalid_utf8(self):
        inputs = json.loads(self.prepared.inputs_json)
        inputs["profile"]["limits"]["artifacts"]["max_file_bytes"] = 100
        prepared = replace(self.prepared, inputs_json=json.dumps(inputs))
        await self.assert_rejected(draft(), "UI_ARTIFACT_TOO_LARGE", prepared=prepared)
        value = draft()
        value["screens"][0]["title"] = "\ud800"
        await self.assert_rejected(value, "UI_INVALID_SPEC")

    async def test_invalid_worker_metadata(self):
        result = await self.generate()
        for values in ({"artifact_id": "not-a-uuid"}, {"duration_seconds": float("nan")}):
            with self.subTest(values=values), self.assertRaisesRegex(UiSpecError, "UI_INVALID_RESULT_METADATA"):
                self.pack(result, **values)

    def test_schema_is_valid(self):
        for schema in (ui_spec_schema(), draft_schema()):
            Draft202012Validator.check_schema(schema)

    def test_preflight(self):
        cases = [
            ("UI_CALL_BUDGET_EXHAUSTED", lambda i: i["context"]["limits"].update(max_calls=0)),
            ("UI_INPUT_NOT_ALLOWED", lambda i: i["context"]["input_artifact_ids"].remove(i["openapi_artifact"]["artifact_id"])),
            ("UI_INVALID_INPUT", lambda i: i["requirements_artifact"].update(checksum="0" * 64)),
            ("UI_INPUT_NOT_RESOLVED", lambda i: i["openapi_artifact"].pop("content")),
            ("UI_INPUT_KIND_MISMATCH", lambda i: i["openapi_artifact"].update(kind="ui_spec")),
            ("UI_UNSUPPORTED_PROFILE", lambda i: i["context"].update(profile_id="web-fastapi-react")),
        ]
        for code, change in cases:
            with self.subTest(code=code):
                inputs = example_inputs()
                change(inputs)
                with self.assertRaisesRegex(UiSpecError, code):
                    prepare_ui_spec(**inputs)

    def test_critical_question_blocks_generation(self):
        content = (ROOT.parents[4] / "contracts/agents/examples/requirements.json").read_text(encoding="utf-8")
        update_artifact(self.inputs, "requirements_artifact", content=content)
        with self.assertRaisesRegex(UiSpecError, "UI_UNRESOLVED_QUESTIONS"):
            prepare_ui_spec(**self.inputs)

    async def test_yaml_input_and_local_refs(self):
        document = json.loads(self.inputs["openapi_artifact"]["content"])
        document["components"]["pathItems"] = {"Tickets": document["paths"]["/tickets"]}
        document["paths"]["/tickets"] = {"$ref": "#/components/pathItems/Tickets"}
        document["components"]["schemas"]["Ticket"]["properties"]["parent"] = {"$ref": "#/components/schemas/Ticket"}
        update_artifact(self.inputs, "openapi_artifact", content=yaml.safe_dump(document, allow_unicode=True),
                        media_type="application/yaml")
        result = await self.generate(prepared=prepare_ui_spec(**self.inputs))
        self.assertTrue(result.ok, result.error)

    def test_invalid_yaml_and_openapi(self):
        cases = [("a: 1\na: 2\n", "UI_OPENAPI_INVALID_YAML"),
                 ("a: &anchor [1]\nb: *anchor", "UI_OPENAPI_YAML_ALIAS"),
                 ("a: !!python/object/apply:os.system [echo]", "UI_OPENAPI_INVALID")]
        for content, code in cases:
            with self.subTest(code=code):
                inputs = example_inputs()
                update_artifact(inputs, "openapi_artifact", content=content, media_type="application/yaml")
                with self.assertRaisesRegex(UiSpecError, code):
                    prepare_ui_spec(**inputs)
        cases = [
            ("UI_CONTRACT_VERSION_MISMATCH", lambda d: d["info"].update(version="2")),
            ("UI_OPENAPI_DUPLICATE_OPERATION_ID", lambda d: d["paths"]["/tickets"]["get"].update(operationId="createTicket")),
            ("UI_OPENAPI_MISSING_OPERATION_ID", lambda d: d["paths"]["/tickets"]["post"].pop("operationId")),
            ("UI_OPENAPI_UNRESOLVED_REF", lambda d: d["components"]["schemas"].update(Bad={"$ref": "#/components/schemas/missing"})),
            ("UI_OPENAPI_EXTERNAL_REF", lambda d: d["components"]["schemas"].update(Bad={"$ref": "https://example.invalid/private"})),
        ]
        for code, change in cases:
            with self.subTest(code=code), self.assertRaisesRegex(UiSpecError, code):
                self.prepare_openapi(change)

    async def test_overlapping_routes_are_rejected_in_both_orders(self):
        pairs = [("/tickets/:id", "/tickets/:ticketId"), ("/tickets/new", "/tickets/:id"),
                 ("/:section/new", "/tickets/:id"), ("/tickets/:id/edit", "/tickets/new/edit"),
                 ("/", "/")]
        for first, second in pairs:
            for routes in ((first, second), (second, first)):
                with self.subTest(routes=routes):
                    value = draft()
                    for screen, route in zip(value["screens"], routes):
                        screen["route"] = route
                    await self.assert_rejected(value, "UI_DUPLICATE_ROUTE")

    async def test_disjoint_routes_are_accepted(self):
        for routes in (("/", "/tickets"), ("/", "/:id"), ("/tickets", "/tickets/:id"),
                       ("/tickets/new", "/tickets/old"), ("/tickets/:id", "/users/:id")):
            with self.subTest(routes=routes):
                value = draft()
                for screen, route in zip(value["screens"], routes):
                    screen["route"] = route
                result = await self.generate(value)
                self.assertTrue(result.ok, result.error)

    async def test_screen_fr_must_have_component_or_action(self):
        value = draft()
        for component in value["screens"][0]["components"]:
            if "FR-004" in component["related_fr_ids"]:
                component["related_fr_ids"].remove("FR-004")
        await self.assert_rejected(value, "UI_SCREEN_FR_UNCOVERED")
        value["screens"][0]["components"][0]["related_fr_ids"].append("FR-004")
        result = await self.generate(value)
        self.assertTrue(result.ok, result.error)

    async def test_action_only_fr_coverage_is_valid(self):
        value = draft()
        for component in value["screens"][0]["components"]:
            if "FR-004" in component["related_fr_ids"]:
                component["related_fr_ids"].remove("FR-004")
        value["screens"][0]["actions"][1]["related_fr_ids"].append("FR-004")
        prepared = self.prepare_openapi(lambda d: d["paths"]["/tickets"]["get"]["x-fr-ids"].append("FR-004"))
        result = await self.generate(value, prepared=prepared)
        self.assertTrue(result.ok, result.error)

    async def test_global_fr_coverage_requires_explicit_non_ui_reason(self):
        def add_requirement(value):
            fr = deepcopy(value["functional_requirements"][0])
            fr.update(id="FR-005", text="Запускать фоновое обслуживание.")
            value["functional_requirements"].append(fr)
            criterion = deepcopy(value["acceptance_criteria"][0])
            criterion.update(id="AC-005", related_fr_ids=["FR-005"])
            value["acceptance_criteria"].append(criterion)

        update_artifact(self.inputs, "requirements_artifact", add_requirement)
        prepared = prepare_ui_spec(**self.inputs)
        await self.assert_rejected(draft(), "UI_INCOMPLETE_FR_COVERAGE", prepared=prepared)
        value = draft()
        value["non_ui_requirements"] = [{"fr_id": "FR-005", "reason": "Фоновая работа без пользовательского действия."}]
        result = await self.generate(value, prepared=prepared)
        self.assertTrue(result.ok, result.error)

    async def test_security_blocks_public_access_without_role_extension(self):
        value = draft()
        value["screens"][0]["access"].update(mode="public", role_ids=[])
        value["screens"][0]["actions"][0]["access"].update(mode="public", role_ids=[])
        prepared = self.prepare_openapi(lambda d: d["paths"]["/tickets"]["post"].pop("x-role-ids"))
        await self.assert_rejected(value, "UI_OPERATION_AUTH_REQUIRED", prepared=prepared)

    async def test_operation_security_can_remove_or_make_auth_optional(self):
        cases = [([], []), ([{}], [{"schemes": []}]),
                 ([{}, {"session": []}], [{"schemes": []}, {"schemes": [{"scheme_id": "session", "scopes": []}]}])]
        for security, expected in cases:
            with self.subTest(security=security):
                def change(document):
                    operation = document["paths"]["/tickets"]["post"]
                    operation.pop("x-role-ids")
                    operation["security"] = security

                value = draft()
                value["screens"][0]["access"].update(mode="public", role_ids=[])
                action = value["screens"][0]["actions"][0]
                action["access"].update(mode="public", role_ids=[])
                action["security"] = expected
                result = await self.generate(value, prepared=self.prepare_openapi(change))
                self.assertTrue(result.ok, result.error)

    async def test_security_preserves_and_or_and_oauth_scopes(self):
        def change(document):
            document["components"]["securitySchemes"].update({
                "oauth": {"type": "oauth2", "flows": {"authorizationCode": {
                    "authorizationUrl": "https://example.invalid/authorize", "tokenUrl": "https://example.invalid/token",
                    "scopes": {"tickets:read": "Read", "tickets:write": "Write"},
                }}},
                "bearer": {"type": "http", "scheme": "bearer"},
            })
            document["paths"]["/tickets"]["post"]["security"] = [
                {"session": [], "oauth": ["tickets:write", "tickets:read"]}, {"bearer": []},
            ]

        prepared = self.prepare_openapi(change)
        value = draft()
        security = [{"schemes": [{"scheme_id": "bearer", "scopes": []}]}, {"schemes": [
            {"scheme_id": "oauth", "scopes": ["tickets:read", "tickets:write"]},
            {"scheme_id": "session", "scopes": []},
        ]}]
        value["screens"][0]["actions"][0]["security"] = security
        result = await self.generate(value, prepared=prepared)
        self.assertTrue(result.ok, result.error)
        for changed in ([], security[:1], [{"schemes": []}],
                        [{"schemes": [security[1]["schemes"][0]]}],
                        [{"schemes": [security[0]["schemes"][0], {"scheme_id": "bearer", "scopes": ["admin"]}]}]):
            with self.subTest(security=changed):
                value["screens"][0]["actions"][0]["security"] = changed
                await self.assert_rejected(value, "UI_SECURITY_MISMATCH", prepared=prepared)

    async def test_non_oauth_role_names_are_preserved(self):
        prepared = self.prepare_openapi(lambda d: d["paths"]["/tickets"]["post"].update(security=[{"session": ["employee"]}]))
        value = draft()
        value["screens"][0]["actions"][0]["security"][0]["schemes"][0]["scopes"] = ["employee"]
        result = await self.generate(value, prepared=prepared)
        self.assertTrue(result.ok, result.error)

    def test_unknown_security_scheme_and_scope_fail_preflight(self):
        with self.assertRaisesRegex(UiSpecError, "UI_OPENAPI_UNKNOWN_SECURITY_SCHEME"):
            self.prepare_openapi(lambda d: d.update(security=[{"missing": []}]))

        def change(document):
            document["components"]["securitySchemes"]["oauth"] = {
                "type": "oauth2", "flows": {"clientCredentials": {
                    "tokenUrl": "https://example.invalid/token", "scopes": {"read": "Read"},
                }},
            }
            document["security"] = [{"oauth": ["write"]}]

        with self.assertRaisesRegex(UiSpecError, "UI_OPENAPI_UNKNOWN_SCOPE"):
            self.prepare_openapi(change)

    async def test_operation_role_and_fr_extensions_still_apply(self):
        value = draft()
        value["screens"][0]["access"]["role_ids"].append("ROLE-002")
        value["screens"][0]["actions"][0]["access"]["role_ids"] = ["ROLE-002"]
        await self.assert_rejected(value, "UI_OPERATION_ACCESS_WIDENED")
        value = draft()
        value["screens"][0]["actions"][0]["related_fr_ids"] = ["FR-002"]
        await self.assert_rejected(value, "UI_OPERATION_FR_MISMATCH")

    async def test_request_bindings_reject_missing_or_unknown_fields(self):
        cases = [
            ("UI_MISSING_REQUEST_BINDING", lambda a: a.update(request_bindings=[])),
            ("UI_UNKNOWN_COMPONENT", lambda a: a["request_bindings"][0].update(component_id="COMP-999")),
            ("UI_UNKNOWN_COMPONENT_FIELD", lambda a: a["request_bindings"][0].update(component_field="missing")),
            ("UI_UNKNOWN_API_FIELD", lambda a: a["request_bindings"][0].update(field_path=["missing"])),
            ("UI_UNKNOWN_MEDIA_TYPE", lambda a: a["request_bindings"][0].update(media_type="text/plain")),
            ("UI_UNKNOWN_PARAMETER", lambda a: a["request_bindings"][0].update(location="query", parameter_name="missing", media_type=None)),
        ]
        for code, change in cases:
            with self.subTest(code=code):
                value = draft()
                change(value["screens"][0]["actions"][0])
                await self.assert_rejected(value, code)
        value = draft()
        value["screens"][1]["actions"][1]["request_bindings"].pop(0)
        await self.assert_rejected(value, "UI_MISSING_REQUEST_BINDING")

    async def test_response_bindings_reject_unknown_fields_and_targets(self):
        cases = [
            ("UI_MISSING_RESPONSE_BINDING", lambda a: a.update(response_bindings=[])),
            ("UI_UNKNOWN_RESPONSE", lambda a: a["response_bindings"][0].update(status_code="202")),
            ("UI_UNKNOWN_API_FIELD", lambda a: a["response_bindings"][0].update(field_path=["missing"])),
            ("UI_UNKNOWN_COMPONENT", lambda a: a["response_bindings"][0].update(component_id="COMP-999")),
            ("UI_UNKNOWN_COMPONENT_FIELD", lambda a: a["response_bindings"][0].update(component_field="missing")),
            ("UI_UNKNOWN_MEDIA_TYPE", lambda a: a["response_bindings"][0].update(media_type="text/plain")),
        ]
        for code, change in cases:
            with self.subTest(code=code):
                value = draft()
                change(value["screens"][0]["actions"][0])
                await self.assert_rejected(value, code)

    async def test_duplicate_binding_destinations_are_rejected(self):
        value = draft()
        bindings = value["screens"][0]["actions"][0]["request_bindings"]
        bindings.append({**bindings[0], "component_id": "COMP-006", "component_field": "created_ticket"})
        await self.assert_rejected(value, "UI_DUPLICATE_REQUEST_BINDING")
        value = draft()
        bindings = value["screens"][0]["actions"][0]["response_bindings"]
        bindings.append({**bindings[0], "field_path": ["id"]})
        await self.assert_rejected(value, "UI_DUPLICATE_RESPONSE_BINDING")

    async def test_nested_required_body_fields_and_local_refs(self):
        def change(document):
            body = document["paths"]["/tickets"]["post"]["requestBody"]
            body["content"]["application/json"]["schema"] = {
                "type": "object", "properties": {"details": {"$ref": "#/components/schemas/Details"}},
                "required": ["details"],
            }
            document["components"]["schemas"]["Details"] = {
                "type": "object", "properties": {"description": {"type": "string"}, "category": {"type": "string"}},
                "required": ["description", "category"],
            }

        prepared = self.prepare_openapi(change)
        value = draft()
        bindings = value["screens"][0]["actions"][0]["request_bindings"]
        bindings[0]["field_path"] = ["details", "description"]
        await self.assert_rejected(value, "UI_MISSING_REQUEST_BINDING", prepared=prepared)
        bindings.append({**bindings[0], "field_path": ["details", "category"]})
        result = await self.generate(value, prepared=prepared)
        self.assertTrue(result.ok, result.error)

    async def test_parameter_inheritance_and_override(self):
        def change(document):
            item = document["paths"]["/tickets/{id}/status"]
            operation = item["patch"]
            parameter = operation["parameters"].pop()
            document["components"]["parameters"] = {"TicketId": parameter}
            item["parameters"] = [{"$ref": "#/components/parameters/TicketId"},
                                  {"name": "notify", "in": "query", "required": True, "schema": {"type": "boolean"}}]
            operation["parameters"] = [{"name": "notify", "in": "query", "required": False, "schema": {"type": "boolean"}}]

        result = await self.generate(prepared=self.prepare_openapi(change))
        self.assertTrue(result.ok, result.error)

    async def test_required_query_header_and_cookie_bindings(self):
        for location in ("query", "header", "cookie"):
            with self.subTest(location=location):
                prepared = self.prepare_openapi(lambda d: d["paths"]["/tickets"]["get"].update(parameters=[{
                    "name": "filter", "in": location, "required": True, "schema": {"type": "string"},
                }]))
                value = draft()
                await self.assert_rejected(value, "UI_MISSING_REQUEST_BINDING", prepared=prepared)
                value["screens"][0]["actions"][1]["request_bindings"] = [{
                    "component_id": "COMP-003", "component_field": "value", "location": location,
                    "parameter_name": "filter", "media_type": None, "field_path": [],
                }]
                result = await self.generate(value, prepared=prepared)
                self.assertTrue(result.ok, result.error)

    async def test_optional_object_still_requires_its_nested_fields_when_bound(self):
        def change(document):
            schema = document["paths"]["/tickets"]["post"]["requestBody"]["content"]["application/json"]["schema"]
            schema["properties"]["details"] = {
                "type": "object", "properties": {"code": {"type": "string"}, "name": {"type": "string"}},
                "required": ["code", "name"],
            }

        prepared = self.prepare_openapi(change)
        value = draft()
        bindings = value["screens"][0]["actions"][0]["request_bindings"]
        bindings.append({**bindings[0], "field_path": ["details", "code"]})
        await self.assert_rejected(value, "UI_MISSING_REQUEST_BINDING", prepared=prepared)
        bindings.append({**bindings[0], "field_path": ["details", "name"]})
        result = await self.generate(value, prepared=prepared)
        self.assertTrue(result.ok, result.error)

    async def test_local_actions_cannot_carry_api_bindings(self):
        value = draft()
        action = value["screens"][0]["actions"][0]
        action.update(kind="local", operation_id=None, request_bindings=[], response_bindings=[], security=[])
        result = await self.generate(value)
        self.assertTrue(result.ok, result.error)
        action["request_bindings"] = draft()["screens"][0]["actions"][0]["request_bindings"]
        await self.assert_rejected(value, "UI_INVALID_ACTION")

    async def test_response_array_fields_and_bodyless_response(self):
        value = draft()
        value["screens"][0]["actions"][1]["response_bindings"][0]["field_path"] = ["*", "status"]
        result = await self.generate(value)
        self.assertTrue(result.ok, result.error)
        value["screens"][0]["actions"][1]["response_bindings"][0]["field_path"] = ["*", "missing"]
        await self.assert_rejected(value, "UI_UNKNOWN_API_FIELD")
        prepared = self.prepare_openapi(lambda d: d["paths"]["/tickets/{id}/status"]["patch"].update(
            responses={"204": {"description": "Updated"}}))
        value = draft()
        value["screens"][1]["actions"][1]["response_bindings"] = []
        result = await self.generate(value, prepared=prepared)
        self.assertTrue(result.ok, result.error)

    async def test_readonly_and_writeonly_fields_cannot_cross_direction(self):
        prepared = self.prepare_openapi(lambda d: d["paths"]["/tickets"]["post"]["requestBody"]["content"]
                                       ["application/json"]["schema"]["properties"]["description"].update(readOnly=True))
        await self.assert_rejected(draft(), "UI_BINDING_DIRECTION", prepared=prepared)
        prepared = self.prepare_openapi(lambda d: d["components"]["schemas"]["Ticket"]["properties"]
                                       ["description"].update(writeOnly=True))
        value = draft()
        value["screens"][0]["actions"][0]["response_bindings"][0]["field_path"] = ["description"]
        await self.assert_rejected(value, "UI_BINDING_DIRECTION", prepared=prepared)

    async def test_unsupported_binding_schema_fails_without_losing_usage(self):
        prepared = self.prepare_openapi(lambda d: d["components"]["schemas"]["Ticket"].update(
            oneOf=[{"type": "object"}, {"type": "null"}]))
        await self.assert_rejected(draft(), "UI_BINDING_UNSUPPORTED_SCHEMA", prepared=prepared)


if __name__ == "__main__":
    unittest.main()
