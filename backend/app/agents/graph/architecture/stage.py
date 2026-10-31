"""A-05 → A-06 → A-07 → A-08 → freeze; no dispatch or publication before freeze."""

from copy import deepcopy
from asyncio import CancelledError
from hashlib import sha256
import re
from time import monotonic
from typing import TypedDict

from jsonschema import Draft202012Validator, FormatChecker
from langgraph.graph import END, START, StateGraph
from langgraph.errors import NodeCancelledError
import yaml

from contracts.agents.validate import ContractError, Contracts, loads

from .freeze import freeze_contracts, validate_artifact, validate_proposal
from .inputs import validate_reservation
from .models import (
    ArchitectureError, ArchitectureNodes, ArchitectureOutcome, NODES,
    NodeReply, NodeRequest, PreparedArchitecture, canonical, require,
)


class ArchitectureExecutionError(RuntimeError):
    """A node did not settle usage; the worker must settle its external reservation."""

    def __init__(self, node, partial_usage):
        super().__init__("ARCHITECTURE_UNSETTLED_NODE")
        self.node = node
        self.partial_usage = deepcopy(partial_usage)


class ArchitectureState(TypedDict):
    inputs_json: str
    reservation: dict
    proposal: dict | None
    artifacts: dict
    usage: dict
    diagnostics: list
    outputs: list
    failed: bool
    document: dict


def empty_usage():
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "duration_seconds": 0, "model_calls": []}


def diagnostic(code, message, retryable=False):
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
        code = "ARCHITECTURE_CHILD_FAILED"
    return {"code": code, "message": message, "severity": "error", "retryable": retryable, "related_fr_ids": []}


class ArchitectureStage:
    """Ephemeral composition under the worker's lease and durable attempt ledger.

    The caller supplies trusted ports. There is no checkpointer, retry, artifact
    storage write, task fan-out or Run.status transition inside this graph.
    """

    def __init__(self, nodes: ArchitectureNodes):
        self.nodes = nodes
        self.contracts = Contracts()
        self.usage_validator = Draft202012Validator(
            {"$ref": self.contracts.schemas["common"]["$id"] + "#/$defs/usage"},
            registry=self.contracts.registry, format_checker=FormatChecker())
        builder = StateGraph(ArchitectureState)
        for name in NODES:
            builder.add_node(name, self._node(name))
        builder.add_node("freeze", self._freeze)
        builder.add_edge(START, NODES[0])
        for current, following in zip(NODES, (*NODES[1:], "freeze")):
            builder.add_conditional_edges(current, lambda state, next_node=following: END if state["failed"] else next_node)
        builder.add_edge("freeze", END)
        self.graph = builder.compile()

    async def run(self, prepared: PreparedArchitecture, *, reservation: dict) -> ArchitectureOutcome:
        inputs = loads(prepared.inputs_json)
        try:
            reservation = validate_reservation(reservation, inputs)
        except ArchitectureError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ArchitectureError("ARCHITECTURE_INVALID_RESERVATION") from None
        started = monotonic()
        state = {"inputs_json": prepared.inputs_json, "reservation": reservation,
                 "proposal": None, "artifacts": {}, "usage": empty_usage(), "diagnostics": [],
                 "outputs": [], "failed": False, "document": {}}
        if inputs["waiting"]:
            status = "waiting_for_input"
        elif inputs["max_calls"] < len(NODES) or inputs["max_output_tokens"] < len(NODES):
            status = "failed"
            state["diagnostics"] = [diagnostic("BUDGET_EXCEEDED", "Архитектор: бюджет не допускает четыре узла архитектуры.")]
        else:
            try:
                state = await self.graph.ainvoke(state, {"recursion_limit": 8})
            except NodeCancelledError:
                raise CancelledError() from None
            status = "failed" if state["failed"] else "succeeded"
        usage = deepcopy(state["usage"])
        usage["duration_seconds"] = monotonic() - started
        result = {key: deepcopy(inputs["context"][key]) for key in (
            "schema_version", "run_id", "stage_id", "attempt", "contract_version")}
        result.update(status=status, output_artifacts=state["outputs"] if status == "succeeded" else [],
                      questions=deepcopy(inputs["requirements"]["questions"]),
                      assumptions=deepcopy(inputs["requirements"]["assumptions"]),
                      usage=usage, diagnostics=state["diagnostics"])
        self.contracts.validate("agent-result", result)
        tree = state["proposal"]["project_tree"] if status == "succeeded" else []
        return ArchitectureOutcome(result, deepcopy(tree))

    def _node(self, name):
        async def invoke(state):
            inputs, reservation = loads(state["inputs_json"]), state["reservation"]
            context = deepcopy(inputs["context"])
            index = NODES.index(name)
            quota, remainder = divmod(inputs["max_output_tokens"], len(NODES))
            context["limits"].update(max_calls=1, max_output_tokens=quota + (index < remainder),
                timeout_seconds=min(inputs["timeout_seconds"] / len(NODES), inputs["profile"]["limits"]["model_call"]["timeout_seconds"]))
            if name != "proposal":
                context["input_artifact_ids"] = [inputs["requirements_artifact"]["artifact_id"]]
            if name == "ui_spec":
                context["input_artifact_ids"].append(state["artifacts"]["openapi"]["artifact_id"])
            slot = reservation["artifacts"].get(name)
            request = NodeRequest(
                context=context, requirements_artifact=deepcopy(inputs["requirements_artifact"]),
                profile_content=inputs["profile_content"], profile_checksum=inputs["profile_checksum"],
                rules_text=inputs["rules_text"], rules_checksum=inputs["rules_checksum"],
                api_policy=deepcopy(inputs["api_policy"]), naming_policy=deepcopy(inputs["naming_policy"]),
                attempt_id=reservation["attempt_id"], artifact_id=slot["artifact_id"] if slot else None,
                artifact_version=slot["version"] if slot else 1, call_id=reservation["call_ids"][name],
                openapi_artifact=deepcopy(state["artifacts"].get("openapi")),
            )
            # Exceptions/cancellation cannot be turned into fictitious zero usage.
            try:
                reply = await self.nodes(name, request)
                require(isinstance(reply, NodeReply), "ARCHITECTURE_INVALID_NODE_REPLY")
                canonical(reply.usage)
                self.usage_validator.validate(reply.usage)
                calls = reply.usage["model_calls"]
                require(reply.usage["calls"] == len(calls)
                        and len({call["call_id"] for call in calls}) == len(calls), "ARCHITECTURE_INVALID_NODE_USAGE")
                for field in ("input_tokens", "output_tokens"):
                    values = [call[field] for call in calls]
                    require(reply.usage[field] == (None if None in values else sum(values)),
                            "ARCHITECTURE_INVALID_NODE_USAGE")
                require(all(call["call_id"] == reservation["call_ids"][name] for call in calls),
                        "ARCHITECTURE_INVALID_NODE_USAGE")
                existing = {call["call_id"] for call in state["usage"]["model_calls"]}
                require(not existing.intersection(call["call_id"] for call in reply.usage["model_calls"]),
                        "ARCHITECTURE_CALL_ID_REUSED")
            except Exception:
                raise ArchitectureExecutionError(name, state["usage"]) from None
            usage = deepcopy(state["usage"])
            usage["calls"] += reply.usage["calls"]
            usage["duration_seconds"] += reply.usage["duration_seconds"]
            usage["model_calls"].extend(deepcopy(reply.usage["model_calls"]))
            for field in ("input_tokens", "output_tokens"):
                values = [call[field] for call in usage["model_calls"]]
                usage[field] = None if None in values else sum(values)
            try:
                require(reply.usage["calls"] <= 1, "BUDGET_EXCEEDED")
                require(reply.usage["output_tokens"] is None or reply.usage["output_tokens"] <= context["limits"]["max_output_tokens"],
                        "BUDGET_EXCEEDED")
                if reply.error_code:
                    return {"usage": usage, "failed": True, "diagnostics": [diagnostic(
                        reply.error_code, "Архитектор: узел " + name + " вернул отказ; контракт не зафиксирован.", reply.retryable)]}
                if name == "proposal":
                    validate_proposal(reply.payload, inputs)
                    return {"proposal": deepcopy(reply.payload), "usage": usage}
                payload = validate_artifact(reply.payload, name, inputs, reservation)
                if name == "data_model":
                    from .naming import validate_names
                    validate_names(state["document"], payload, inputs["naming_policy"])
                artifacts = {**deepcopy(state["artifacts"]), name: deepcopy(reply.payload)}
                if name == "openapi":
                    require(not any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(reply.payload["content"])),
                            "ARCHITECTURE_INVALID_OPENAPI")
                    document = yaml.safe_load(reply.payload["content"])
                    require(document["openapi"] == "3.1.0" and document["info"]["version"] == context["contract_version"],
                            "ARCHITECTURE_CONTRACT_VERSION_MISMATCH")
                    require(document["x-requirements-artifact-id"] == inputs["requirements_artifact"]["artifact_id"]
                            and document["x-profile-id"] == context["profile_id"]
                            and document["x-api-policy"]["checksum"] == sha256(canonical(inputs["api_policy"]).encode()).hexdigest(),
                            "ARCHITECTURE_SOURCE_MISMATCH")
                    return {"artifacts": artifacts, "document": document, "usage": usage}
                return {"artifacts": artifacts, "usage": usage}
            except ArchitectureError as error:
                return {"usage": usage, "failed": True, "diagnostics": [diagnostic(str(error), error.message)]}
            except (ContractError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
                return {"usage": usage, "failed": True, "diagnostics": [diagnostic(
                    "ARCHITECTURE_INVALID_NODE_OUTPUT", "Архитектор: некорректный результат узла " + name + ".")]}
        return invoke

    def _freeze(self, state):
        try:
            outputs = freeze_contracts(state["proposal"], state["artifacts"], loads(state["inputs_json"]),
                                       state["reservation"], state["document"])
            return {"outputs": outputs}
        except ArchitectureError as error:
            return {"failed": True, "diagnostics": [diagnostic(str(error), error.message)]}
        except (ContractError, ValueError, TypeError, KeyError, AttributeError):
            return {"failed": True, "diagnostics": [diagnostic(
                "ARCHITECTURE_FREEZE_FAILED", "Архитектор: набор контрактов не прошёл общую проверку.")]}
