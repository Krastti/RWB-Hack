"""A-05 checks through the real A-01 adapter and a local HTTP transport."""

import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from jsonschema import Draft202012Validator

from backend.app.agents.llm import LLMAdapter, LLMConfig
from backend.app.agents.nodes.architecture import ArchitectureNode
from backend.app.agents.nodes.architecture.prompt import response_schema
from contracts.agents.validate import Contracts, load, load_profile_rules, validate_profile_grants


ROOT = Path(__file__).resolve().parents[5]
EXAMPLES = ROOT / "contracts/agents/examples"


class ArchitectureNodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = load(EXAMPLES / "agent-input.architecture.json")
        self.context["profile_id"] = "web-fastapi-angular"
        self.artifact = load(EXAMPLES / "agent-result.succeeded.json")["output_artifacts"][0]
        self.profile_bytes = (ROOT / "templates/profiles/web-fastapi-angular/profile.json").read_bytes()
        self.profile = json.loads(self.profile_bytes)
        self.rules = load_profile_rules(self.profile)
        self.design = {key: value for key, value in load(EXAMPLES / "architecture.json").items()
                       if key in {"components", "decisions", "integrations"}}
        self.requests = []
        self.usage = {"prompt_tokens": 125, "completion_tokens": 250, "total_tokens": 375}
        self.status = 200
        self.config = LLMConfig(base_url="https://model.invalid/v1", model="test-model",
                                api_key="test-only-placeholder", timeout_seconds=180, max_output_tokens=9000)

    async def respond(self, request):
        self.requests.append(json.loads(request.content))
        return httpx.Response(self.status, json={
            "choices": [{"message": {"content": json.dumps(self.design, ensure_ascii=False)},
                         "finish_reason": "stop"}], "usage": self.usage,
        })

    async def generate(self, **overrides):
        arguments = {"requirements_artifact": self.artifact, "profile_bytes": self.profile_bytes,
                     "profile_checksum": sha256(self.profile_bytes).hexdigest(),
                     "rules_text": self.rules, "rules_checksum": sha256(self.rules.encode()).hexdigest()}
        arguments.update(overrides)
        node = ArchitectureNode(self.config, transport=httpx.MockTransport(self.respond))
        return await node.generate(self.context, **arguments)

    def assert_error(self, result, code, *, calls):
        self.assertFalse(result.ok)
        self.assertIsNone(result.data)
        self.assertEqual(result.error.code, code)
        self.assertEqual(result.usage.calls, calls)
        self.assertEqual(len(self.requests), calls)

    async def test_generates_boundaries_tree_and_granted_plan_from_analysis(self):
        before = deepcopy((self.context, self.artifact, self.design))
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        proposal = result.data
        self.assertFalse(proposal["ready_for_generation"])
        self.assertNotIn("contracts", proposal["architecture_core"])
        self.assertEqual(proposal["requires_contracts"], ["openapi", "data_model", "ui_spec"])
        core = proposal["architecture_core"]
        requirements = json.loads(self.artifact["content"])
        self.assertEqual(core["in_scope"], [i["text"] for i in requirements["in_scope"]])
        self.assertEqual(core["out_of_scope"], [i["text"] for i in requirements["out_of_scope"]])
        self.assertEqual(core["requirements_artifact_id"], self.artifact["artifact_id"])
        plan = proposal["generation_plan"]
        Contracts().validate("generation-plan", plan)
        validate_profile_grants(plan, self.profile)
        self.assertEqual(plan["contract_version"], self.context["contract_version"])
        self.assertEqual(len({t["stage_id"] for t in plan["tasks"]}), len(plan["tasks"]))
        for task in plan["tasks"]:
            self.assertEqual(task["input_artifact_ids"], [self.artifact["artifact_id"]])
        backend = next(t for t in plan["tasks"] if t["owner_agent"] == "backend")
        self.assertNotIn("backend", backend["output_directories"])
        self.assertNotIn("backend/migrations", backend["output_directories"])
        tree = {entry["path"]: entry for entry in proposal["project_tree"]}
        self.assertEqual(tree["frontend/angular.json"]["owner_agent"], "frontend")
        self.assertEqual(tree["backend/migrations/env.py"]["owner_agent"], "migrations")
        self.assertNotIn("integrations", tree)
        self.assertEqual(result.usage.tokens_in, 125)
        self.assertEqual(result.usage.tokens_out, 250)
        self.assertEqual((self.context, self.artifact, self.design), before)

    async def test_profile_and_contract_provenance_are_preserved(self):
        self.context["contract_version"] = "frozen-v3"
        result = await self.generate()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["contract_version"], "frozen-v3")
        self.assertEqual(result.data["architecture_core"]["contract_version"], "frozen-v3")
        self.assertEqual(result.data["generation_plan"]["contract_version"], "frozen-v3")
        self.assertEqual(result.data["profile_checksum"], sha256(self.profile_bytes).hexdigest())
        self.assertEqual(result.data["requirements_checksum"], self.artifact["checksum"])

    async def test_schema_validation_never_fetches_remote_references(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected schema download")), \
             patch("socket.getaddrinfo", side_effect=AssertionError("Unexpected DNS request")):
            result = await self.generate()
        self.assertTrue(result.ok, result.error)

    async def test_required_integration_cannot_be_silently_omitted(self):
        requirements = json.loads(self.artifact["content"])
        requirements["integrations"] = [{"text": "Notification service adapter",
                                         "evidence": requirements["product_goals"][0]["evidence"]}]
        self.artifact["content"] = json.dumps(requirements, ensure_ascii=False)
        self.artifact["size_bytes"] = len(self.artifact["content"].encode())
        self.artifact["checksum"] = sha256(self.artifact["content"].encode()).hexdigest()
        self.assert_error(await self.generate(), "ARCHITECTURE_INTEGRATIONS_LOST", calls=1)

    async def test_plan_and_tree_are_deterministic(self):
        first, second = await self.generate(), await self.generate()
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(first.data, second.data)

    async def test_task_fr_scopes_are_independent(self):
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        tasks = result.data["generation_plan"]["tasks"]
        expected = deepcopy([t["related_fr_ids"] for t in tasks])
        tasks[0]["related_fr_ids"].remove("FR-004")
        self.assertNotIn("FR-004", tasks[0]["related_fr_ids"])
        self.assertEqual([t["related_fr_ids"] for t in tasks[1:]], expected[1:])

    async def test_architecture_directory_does_not_expand_coding_grants(self):
        self.design["components"].append({
            "id": "CMP-003", "name": "API contracts",
            "responsibilities": ["Versioned event schemas"],
            "output_directories": ["contracts/events"], "depends_on": [],
            "related_fr_ids": ["FR-001"],
        })
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        tree = {entry["path"]: entry for entry in result.data["project_tree"]}
        self.assertEqual(tree["contracts/events"]["type"], "directory")
        plan = result.data["generation_plan"]
        validate_profile_grants(plan, self.profile)
        for task in plan["tasks"]:
            self.assertNotEqual(task["owner_agent"], "architecture")
            self.assertFalse(any(path == "contracts" or path.startswith("contracts/")
                                 for path in task["output_paths"] + task["output_directories"]))

    async def test_effective_limits_reach_the_transport(self):
        self.context["limits"].update(max_output_tokens=400, timeout_seconds=7)
        with patch("backend.app.agents.nodes.architecture.node.LLMAdapter", wraps=LLMAdapter) as adapter:
            result = await self.generate()
        self.assertTrue(result.ok)
        config = adapter.call_args.args[0]
        self.assertEqual(config.timeout_seconds, 7)
        self.assertEqual(config.max_output_tokens, 400)
        self.assertEqual(self.requests[0]["max_tokens"], 400)

    async def test_zero_call_budget_blocks_before_network(self):
        self.context["limits"]["max_calls"] = 0
        self.assert_error(await self.generate(), "ARCHITECTURE_CALL_BUDGET_EXHAUSTED", calls=0)

    def freeze_profile(self):
        self.profile_bytes = json.dumps(self.profile).encode()

    async def test_zero_profile_call_budget_blocks_before_network(self):
        self.profile["limits"]["agent"]["max_calls"] = 0
        self.freeze_profile()
        self.assert_error(await self.generate(), "ARCHITECTURE_CALL_BUDGET_EXHAUSTED", calls=0)

    async def test_integral_json_token_limits_are_normalized(self):
        for source in (self.context["limits"], self.profile["limits"]["agent"]):
            with self.subTest(source=source):
                source["max_output_tokens"] = 400.0
                self.freeze_profile()
                with patch("backend.app.agents.nodes.architecture.node.LLMAdapter", wraps=LLMAdapter) as adapter:
                    result = await self.generate()
                self.assertTrue(result.ok, result.error)
                self.assertIs(type(adapter.call_args.args[0].max_output_tokens), int)
                self.assertEqual(self.requests[-1]["max_tokens"], 400)
                source["max_output_tokens"] = 9000

    async def test_invalid_effective_config_returns_error_before_network(self):
        with patch("backend.app.agents.nodes.architecture.node.replace", side_effect=ValueError("invalid")):
            self.assert_error(await self.generate(), "ARCHITECTURE_INVALID_INPUT", calls=0)

    async def test_required_artifact_owner_must_have_a_grant_before_network(self):
        for condition in ("always", "has_integrations"):
            with self.subTest(condition=condition):
                self.profile["required_artifacts"].append({
                    "path": "reports/output.json", "kind": "source",
                    "owner": "frontend", "condition": condition,
                })
                self.freeze_profile()
                self.assert_error(await self.generate(), "ARCHITECTURE_REQUIRED_ARTIFACT_NOT_ALLOWED", calls=0)
                self.profile["required_artifacts"].pop()

    async def test_required_artifact_respects_directory_boundaries(self):
        self.profile["required_artifacts"].append({
            "path": "frontend-other/output.json", "kind": "source",
            "owner": "frontend", "condition": "always",
        })
        self.freeze_profile()
        self.assert_error(await self.generate(), "ARCHITECTURE_REQUIRED_ARTIFACT_NOT_ALLOWED", calls=0)

    async def test_profile_path_limit_applies_to_component_directories(self):
        self.profile["limits"]["artifacts"]["max_path_length"] = 100
        self.freeze_profile()
        self.design["components"][0]["output_directories"] = ["frontend/" + "x" * 150]
        self.assert_error(await self.generate(), "ARCHITECTURE_PATH_TOO_LONG", calls=1)

    async def test_profile_path_limit_applies_to_required_files(self):
        self.profile["limits"]["artifacts"]["max_path_length"] = 100
        self.profile["required_artifacts"].append({
            "path": "frontend/" + "x" * 100, "kind": "source",
            "owner": "frontend", "condition": "always",
        })
        self.freeze_profile()
        # Full frozen-profile policy validation now rejects the path before dispatch.
        self.assert_error(await self.generate(), "ARCHITECTURE_PATH_TOO_LONG", calls=0)

    async def test_profile_path_limit_accepts_boundary(self):
        self.profile["limits"]["artifacts"]["max_path_length"] = 100
        self.freeze_profile()
        path = "frontend/" + "x" * 91
        self.design["components"][0]["output_directories"] = [path]
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        self.assertIn(path, {entry["path"] for entry in result.data["project_tree"]})

    async def test_transport_schema_contains_only_reachable_definitions(self):
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        schema = self.requests[0]["response_format"]["json_schema"]["schema"]
        self.assertEqual(set(schema["$defs"]), {"fr_id", "relative_path"})

    def test_response_schema_keeps_transitive_references(self):
        contracts = Contracts()
        definitions = contracts.schemas["common"]["$defs"]
        definitions["fr_id_value"] = definitions["fr_id"]
        definitions["fr_id"] = {"$ref": "#/$defs/fr_id_value"}
        before = deepcopy(contracts.schemas)
        schema = response_schema(contracts)
        self.assertEqual(set(schema["$defs"]), {"fr_id", "fr_id_value", "relative_path"})
        Draft202012Validator(schema).validate(self.design)
        self.assertEqual(contracts.schemas, before)

    async def test_unanswered_required_question_blocks_before_network(self):
        waiting = load(EXAMPLES / "agent-result.waiting.json")["output_artifacts"][0]
        self.context["input_artifact_ids"] = [waiting["artifact_id"]]
        self.assert_error(await self.generate(requirements_artifact=waiting),
                          "ARCHITECTURE_BLOCKED_BY_QUESTIONS", calls=0)

    async def test_missing_input_reference_blocks_before_network(self):
        self.context["input_artifact_ids"] = [self.artifact["attempt_id"]]
        self.assert_error(await self.generate(), "ARCHITECTURE_REQUIREMENTS_NOT_ALLOWED", calls=0)

    async def test_checksum_mismatch_blocks_before_network(self):
        self.artifact["checksum"] = "0" * 64
        self.assert_error(await self.generate(), "ARCHITECTURE_INVALID_INPUT", calls=0)

    async def test_profile_bytes_must_match_the_frozen_checksum(self):
        self.assert_error(await self.generate(profile_checksum="0" * 64),
                          "ARCHITECTURE_PROFILE_CHECKSUM_MISMATCH", calls=0)

    async def test_profile_version_cannot_change_silently(self):
        profile = deepcopy(self.profile)
        profile["profile_version"] = "2.0.0"
        raw = json.dumps(profile).encode()
        self.assert_error(await self.generate(profile_bytes=raw, profile_checksum=sha256(raw).hexdigest()),
                          "ARCHITECTURE_INVALID_PROFILE", calls=0)

    async def test_rules_must_match_the_frozen_checksum(self):
        self.assert_error(await self.generate(rules_checksum="0" * 64),
                          "ARCHITECTURE_RULES_CHECKSUM_MISMATCH", calls=0)

    async def test_unknown_fr_is_rejected_and_usage_preserved(self):
        self.design["components"][0]["related_fr_ids"].append("FR-999")
        result = await self.generate()
        self.assert_error(result, "ARCHITECTURE_UNKNOWN_FR", calls=1)
        self.assertEqual(result.usage.tokens_out, 250)

    async def test_every_fr_needs_component_coverage(self):
        for component in self.design["components"]:
            component["related_fr_ids"].remove("FR-004")
        self.assert_error(await self.generate(), "ARCHITECTURE_UNCOVERED_FR", calls=1)

    async def test_component_dependency_cycle_is_rejected(self):
        self.design["components"][1]["depends_on"] = [self.design["components"][0]["id"]]
        self.assert_error(await self.generate(), "ARCHITECTURE_INVALID_DESIGN", calls=1)

    async def test_model_cannot_assign_task_owner(self):
        self.design["components"][0]["owner_agent"] = "assembly"
        self.assert_error(await self.generate(), "LLM_INVALID_RESPONSE", calls=1)

    async def test_unsafe_directory_is_rejected_by_the_response_schema(self):
        self.design["components"][0]["output_directories"] = ["../outside"]
        self.assert_error(await self.generate(), "LLM_INVALID_RESPONSE", calls=1)

    async def test_ungranted_component_directory_is_rejected(self):
        self.design["components"][0]["output_directories"] = ["secrets"]
        self.assert_error(await self.generate(), "ARCHITECTURE_COMPONENT_PATH_NOT_ALLOWED", calls=1)

    async def test_case_collision_is_rejected(self):
        self.design["components"][0]["output_directories"] = ["frontend/SRC"]
        self.assert_error(await self.generate(), "ARCHITECTURE_PATH_COLLISION", calls=1)

    async def test_file_cannot_become_a_directory(self):
        self.design["components"][0]["output_directories"] = ["frontend/package.json/child"]
        self.assert_error(await self.generate(), "ARCHITECTURE_FILE_DIRECTORY_CONFLICT", calls=1)

    async def test_integration_creates_separate_owner_and_conditional_tree(self):
        self.design["integrations"] = [{
            "id": "INT-001", "name": "Notifications", "mode": "mock_adapter",
            "description": "Proposed external notification adapter.", "related_fr_ids": ["FR-001"],
        }]
        result = await self.generate()
        self.assertTrue(result.ok, result.error)
        plan = result.data["generation_plan"]
        integration = next(t for t in plan["tasks"] if t["owner_agent"] == "integrations")
        self.assertIn("backend/app/integrations", integration["output_directories"])
        self.assertIn("integrations/README.md", {p["path"] for p in result.data["project_tree"]})
        validate_profile_grants(plan, self.profile)

    async def test_unknown_token_counts_remain_unknown(self):
        self.usage = None
        result = await self.generate()
        self.assertTrue(result.ok)
        self.assertEqual(result.usage.calls, 1)
        self.assertIsNone(result.usage.tokens_in)
        self.assertIsNone(result.usage.tokens_out)

    async def test_reported_output_over_budget_is_not_accepted(self):
        self.context["limits"]["max_output_tokens"] = 200
        result = await self.generate()
        self.assert_error(result, "ARCHITECTURE_OUTPUT_BUDGET_EXCEEDED", calls=1)
        self.assertEqual(result.usage.tokens_out, 250)

    async def test_transport_failure_does_not_retry(self):
        self.status = 503
        result = await self.generate()
        self.assert_error(result, "LLM_UNAVAILABLE", calls=1)
        self.assertNotIn("choices", result.error.message)

    async def test_cancellation_propagates(self):
        async def cancel(request):
            raise asyncio.CancelledError()

        node = ArchitectureNode(self.config, transport=httpx.MockTransport(cancel))
        with self.assertRaises(asyncio.CancelledError):
            await node.generate(self.context, requirements_artifact=self.artifact,
                                profile_bytes=self.profile_bytes,
                                profile_checksum=sha256(self.profile_bytes).hexdigest(),
                                rules_text=self.rules, rules_checksum=sha256(self.rules.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
