"""Bindings to the real A-05/A-06/A-07/A-08 APIs; missing dependencies fail closed."""

from copy import deepcopy
from dataclasses import replace
import re
from time import monotonic

from contracts.agents.validate import ContractError, Contracts, loads
from backend.app.agents.llm import LLMAdapter, LLMConfig

from .models import NodeReply, require
from .stage import empty_usage


def _modules():
    from ...nodes.architecture import ArchitectureNode
    from ...generators.openapi import prepare_openapi, generate_openapi
    from ...generators.openapi.policy import validate_api_policy
    from ...generators.data_model import build_data_model_prompt, generate_data_model, make_data_model_artifact
    from ...generators.ui_spec import prepare_ui_spec, generate_ui_spec, build_agent_result
    return locals()


def _usage(result, request, model):
    usage = result.usage
    if not usage.calls:
        return empty_usage()
    outcome = "succeeded" if result.ok else "timeout" if result.error.code == "LLM_TIMEOUT" else "failed"
    return {"calls": usage.calls, "input_tokens": usage.tokens_in, "output_tokens": usage.tokens_out,
            "duration_seconds": usage.latency_ms / 1000,
            "model_calls": [{"call_id": request.call_id, "model": model, "outcome": outcome,
                             "input_tokens": usage.tokens_in, "output_tokens": usage.tokens_out,
                             "duration_seconds": usage.latency_ms / 1000}]}


def _code(error, fallback):
    value = getattr(error, "code", str(error))
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value) else fallback


class RuntimeNodes:
    """Use one immutable LLM config; optional transports are explicit caller fixtures."""

    def __init__(self, config: LLMConfig, *, transports=None):
        self.config = config
        self.transports = dict(transports or {})

    async def __call__(self, node, request):
        try:
            modules = _modules()
        except ModuleNotFoundError:
            return NodeReply(None, empty_usage(), "ARCHITECTURE_DEPENDENCIES_UNAVAILABLE")
        require(node in {"proposal", "openapi", "data_model", "ui_spec"}, "ARCHITECTURE_NODE_NOT_ALLOWED")
        config = replace(self.config,
                         max_output_tokens=min(self.config.max_output_tokens, request.context["limits"]["max_output_tokens"]),
                         timeout_seconds=min(self.config.timeout_seconds, request.context["limits"]["timeout_seconds"]))
        profile = loads(request.profile_content)
        arguments = {"attempt_id": request.attempt_id, "artifact_id": request.artifact_id,
                     "artifact_version": request.artifact_version}
        if node == "proposal":
            try:
                modules["validate_api_policy"](Contracts(), request.api_policy,
                    loads(request.requirements_artifact["content"]), request.requirements_artifact["checksum"])
            except ValueError as error:
                return NodeReply(None, empty_usage(), _code(error, "ARCHITECTURE_INVALID_API_POLICY"))
            result = await modules["ArchitectureNode"](config, transport=self.transports.get(node),
                stage_id=request.context["stage_id"]).generate(request.context,
                requirements_artifact=request.requirements_artifact,
                profile_bytes=request.profile_content.encode("utf-8"), profile_checksum=request.profile_checksum,
                rules_text=request.rules_text, rules_checksum=request.rules_checksum)
            return NodeReply(result.data, _usage(result, request, config.model),
                             None if result.ok else result.error.code, False if result.ok else result.error.retryable)
        try:
            if node == "openapi":
                prepared = modules["prepare_openapi"](request.context, request.requirements_artifact,
                                                       profile=profile, api_policy=request.api_policy)
            elif node == "data_model":
                prepared = modules["build_data_model_prompt"](request.context, request.requirements_artifact, profile=profile)
            else:
                prepared = modules["prepare_ui_spec"](request.context,
                    requirements_artifact=request.requirements_artifact, openapi_artifact=request.openapi_artifact,
                    profile_content=request.profile_content)
        except (ContractError, ValueError, KeyError, TypeError) as error:
            return NodeReply(None, empty_usage(), _code(error, "ARCHITECTURE_CHILD_INPUT_REJECTED"))
        adapter = LLMAdapter(config, transport=self.transports.get(node))
        if node == "openapi":
            envelope = await modules["generate_openapi"](adapter, prepared, model=config.model, **arguments)
            usage = deepcopy(envelope["usage"])
            if usage["model_calls"]:
                usage["model_calls"][0]["call_id"] = request.call_id
            ok = envelope["status"] == "succeeded"
            code = None if ok else next((d["code"] for d in envelope["diagnostics"]), "ARCHITECTURE_CHILD_FAILED")
            retryable = any(d["retryable"] for d in envelope["diagnostics"])
            return NodeReply(envelope["output_artifacts"][0] if ok else None, usage, code, retryable)
        started = monotonic()
        result = await modules["generate_data_model" if node == "data_model" else "generate_ui_spec"](adapter, prepared)
        usage = _usage(result, request, config.model)
        if not result.ok:
            return NodeReply(None, usage, result.error.code, result.error.retryable)
        try:
            if node == "data_model":
                artifact = modules["make_data_model_artifact"](result.data, prepared,
                    artifact_id=request.artifact_id, attempt_id=request.attempt_id, version=request.artifact_version)
            else:
                envelope = modules["build_agent_result"](prepared, result, **arguments,
                    call_id=request.call_id, model=config.model, duration_seconds=monotonic() - started)
                artifact = envelope["output_artifacts"][0]
        except (ContractError, ValueError, KeyError, TypeError) as error:
            return NodeReply(None, usage, _code(error, "ARCHITECTURE_CHILD_ARTIFACT_REJECTED"))
        return NodeReply(artifact, usage)
