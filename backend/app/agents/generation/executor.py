"""Execute one accepted generation task without owning StageAttempt or retries."""
import asyncio
from dataclasses import dataclass, replace
from copy import deepcopy
from hashlib import sha256
import json
import time
from typing import Callable, Protocol
from uuid import UUID, uuid4, uuid5

import httpx
from jsonschema import Draft202012Validator

from contracts.agents.validate import ContractError, Contracts
from ..context import PreparedContext
from ..profiles import FrozenProfile, ProfileError
from ..llm import LLMAdapter, LLMConfig, LLMResult
from ..llm.adapter import request_payload


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class GenerationError(ValueError):
    def __init__(self, code: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _require(value, code):
    if not value:
        raise GenerationError(code)


@dataclass(frozen=True)
class FileSpec:
    path: str
    kind: str
    media_type: str
    version: int
    output_token_budget: int


@dataclass(frozen=True)
class GenerationOutcome:
    result: dict
    remaining_paths: tuple[str, ...]


class BatchStore(Protocol):
    def open_attempt(self, *, owner_id: str, run_id: str, attempt_id: str, fingerprint: str) -> None:
        """Authorize scope/lease; atomically bind this attempt to one immutable fingerprint."""
        ...

    def receipt(self, *, owner_id: str, run_id: str, attempt_id: str, batch_id: str) -> dict | None:
        """Return accepted immutable receipt including verified inline bytes, or None."""
        ...

    def accept(self, *, owner_id: str, run_id: str, attempt_id: str, batch_id: str, receipt: dict) -> None:
        """Atomically save all complete batch files + receipt. Exact replay is a no-op.

        Enforce Run/attempt/owner/grants, UUID/path/version/content conflicts and
        lease/fencing; no partially published receipt. Never replace accepted bytes.
        """
        ...


class BudgetLedger(Protocol):
    def reserve(self, *, run_id: str, attempt_id: str, call_id: str, model: str,
                system: str, prompt: str, schema: dict, max_output_tokens: int,
                timeout_seconds: float, agent_limits: dict) -> None:
        """Atomically enforce remaining Run/attempt calls, input/output and time budgets.

        Count/reserve the actual provider envelope including A-01's schema instruction.
        Raise GenerationError before dispatch when exhausted; unknown usage keeps reserve.
        """
        ...

    def finish(self, *, run_id: str, attempt_id: str, call_id: str, result: LLMResult) -> None:
        """Durably account A-01 usage even if response/file validation later fails."""
        ...

    def abandon(self, *, run_id: str, attempt_id: str, call_id: str) -> None:
        """Durably mark interrupted/in-doubt dispatch; never convert unknown usage to zero."""
        ...

    def usage(self, *, run_id: str, attempt_id: str) -> dict:
        """C-02 snapshot including durable, unique physical call IDs and outcomes.

        Successful calls must remain available for receipt provenance checks.
        """
        ...


class BatchExecutor:
    def __init__(self, *, config: LLMConfig, store: BatchStore, ledger: BudgetLedger,
                 max_files_per_batch: int, max_request_bytes: int,
                 validate_file: Callable[[FileSpec, str, PreparedContext, tuple[dict, ...]], None],
                 transport: httpx.AsyncBaseTransport | None = None):
        _require(type(max_files_per_batch) is int and max_files_per_batch > 0
                 and type(max_request_bytes) is int and max_request_bytes > 0, "GENERATION_INVALID_LIMIT")
        self._config, self._store, self._ledger = config, store, ledger
        self._batch_size, self._request_bytes = max_files_per_batch, max_request_bytes
        self._validate_file, self._transport = validate_file, transport
        self._contracts = Contracts()

    async def run(self, *, request: dict, owner_id: str, attempt_id: str, task_id: str,
                  plan: dict, profile: FrozenProfile, context: PreparedContext,
                  files: tuple[FileSpec, ...], model_configuration: bytes) -> GenerationOutcome:
        try:
            self._contracts.validate("agent-input", request)
            UUID(owner_id); UUID(attempt_id)
            profile.validate_plan(plan)
            tasks = [task for task in plan["tasks"] if task["task_id"] == task_id]
            _require(len(tasks) == 1, "GENERATION_TASK_NOT_FOUND")
            task = tasks[0]
            _require(task["stage_id"] == request["stage_id"]
                     and plan["contract_version"] == request["contract_version"]
                     and plan["profile_id"] == request["profile_id"] == profile.profile_id,
                     "GENERATION_TASK_MISMATCH")
            prepared = json.loads(context.data)
            _require(prepared["agent_input"] == request and prepared["owner_agent"] == task["owner_agent"]
                     and context.manifest["profile"] == profile.metadata
                     and set(task["input_artifact_ids"]) <= set(request["input_artifact_ids"]),
                     "GENERATION_CONTEXT_MISMATCH")
            actual_configuration = {key: value for key, value in vars(self._config).items() if key != "api_key"}
            _require(type(model_configuration) is bytes
                     and sha256(model_configuration).hexdigest() == context.manifest["execution_inputs"]["model_configuration"]["checksum"]
                     and json.loads(model_configuration) == actual_configuration,
                     "GENERATION_MODEL_CONFIGURATION_CHANGED")
            requirements = [json.loads(a["content"]) for a in prepared["artifacts"]
                            if a["metadata"]["kind"] == "requirements"]
            _require(len(requirements) == 1 and not any(q["required"] and "answer" not in q
                     for q in requirements[0]["questions"]), "GENERATION_UNRESOLVED_REQUIREMENTS")
            _require(set(task["related_fr_ids"]) <= {f["id"] for f in requirements[0]["functional_requirements"]},
                     "GENERATION_UNKNOWN_FR")
            limits = profile.limits(task["owner_agent"])
            _require(bool(files) and len(files) <= limits["artifacts"]["max_files"], "GENERATION_FILE_LIMIT")
            _require(len({file.path for file in files}) == len(files), "GENERATION_DUPLICATE_FILE")
            expected = {file.path for file in files}
            _require(set(task["output_paths"]) <= expected, "GENERATION_REQUIRED_OUTPUT_MISSING")
            for spec in files:
                _require(type(spec.version) is int and spec.version >= 1
                         and type(spec.output_token_budget) is int and spec.output_token_budget > 0,
                         "GENERATION_INVALID_FILE_SPEC")
                _require(profile.allows(task["owner_agent"], spec.path) and
                         (spec.path in task["output_paths"] or any(spec.path.startswith(d + "/")
                          for d in task["output_directories"])), "GENERATION_OUTPUT_NOT_ALLOWED")
                required_kind = next((a["kind"] for a in profile.data["required_artifacts"] if a["path"] == spec.path), None)
                _require(required_kind is None or spec.kind == required_kind, "GENERATION_OUTPUT_KIND_MISMATCH")
                # Metadata schema checks also cover kind/media type before dispatch.
                self._artifact(spec, "", attempt_id, task, validate_content=False)
            _require(not any(a != b and b.startswith(a + "/") for a in expected for b in expected),
                     "GENERATION_FILE_DIRECTORY_CONFLICT")
        except (ContractError, ProfileError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, GenerationError):
                raise
            raise GenerationError("GENERATION_INVALID_INPUT") from None
        cap = min(self._config.max_output_tokens, request["limits"]["max_output_tokens"],
                  limits["agent"]["max_output_tokens"])
        batches = self._partition(files, cap, context, task)
        configuration = {"task": task, "request": request, "attempt_id": attempt_id,
                         "profile": profile.metadata, "context_checksum": context.checksum, "execution_fingerprint": context.execution_fingerprint,
                         "files": [vars(spec) for spec in files], "model_configuration": actual_configuration,
                         "batch_size": self._batch_size, "request_bytes": self._request_bytes,
                         "output_cap": cap, "executor_version": "1.0.0"}
        fingerprint = sha256(_json(configuration).encode()).hexdigest()
        scope = {"owner_id": owner_id, "run_id": request["run_id"], "attempt_id": attempt_id}
        accounting = {"run_id": request["run_id"], "attempt_id": attempt_id}
        self._store.open_attempt(**scope, fingerprint=fingerprint)
        accepted, diagnostic, retryable = [], None, False
        deadline = time.monotonic() + min(request["limits"].get("timeout_seconds", limits["agent"]["timeout_seconds"]),
                                         limits["agent"]["timeout_seconds"])
        for index, batch in enumerate(batches):
            batch_id = str(uuid5(UUID(attempt_id), f"{fingerprint}:{index}"))
            batch_fingerprint = sha256(_json({"attempt": fingerprint, "files": [vars(s) for s in batch]}).encode()).hexdigest()
            try:
                receipt = self._store.receipt(**scope, batch_id=batch_id)
                if receipt is not None:
                    artifacts = self._verify_receipt(receipt, batch_fingerprint, batch, attempt_id, task,
                                                     self._ledger.usage(**accounting))
                    _require(all(a["size_bytes"] <= limits["artifacts"]["max_file_bytes"] for a in artifacts)
                             and sum(a["size_bytes"] for a in accepted + artifacts)
                             <= limits["artifacts"]["max_total_bytes"], "GENERATION_FILE_LIMIT")
                    accepted.extend(artifacts)
                    continue
                _require(time.monotonic() < deadline, "GENERATION_TIMEOUT")
                output_cap = sum(s.output_token_budget for s in batch) + 256
                timeout = min(self._config.timeout_seconds, limits["model_call"]["timeout_seconds"],
                              deadline - time.monotonic())
                schema = self._schema(batch)
                prompt = self._prompt(context, batch, accepted, task)
                call_config = replace(self._config, max_output_tokens=output_cap, timeout_seconds=timeout)
                _require(self._request_size(call_config, context.system, prompt, schema) <= self._request_bytes,
                         "GENERATION_CONTEXT_LIMIT")
                call_id = str(uuid4())
                prior_usage = self._ledger.usage(**accounting)
                _require(prior_usage["calls"] < min(request["limits"]["max_calls"], limits["agent"]["max_calls"]),
                         "GENERATION_CALL_BUDGET_EXHAUSTED")
                if prior_usage["output_tokens"] is not None:
                    _require(prior_usage["output_tokens"] + output_cap <= min(
                        request["limits"]["max_output_tokens"], limits["agent"]["max_output_tokens"]),
                        "GENERATION_OUTPUT_BUDGET_EXHAUSTED")
                self._ledger.reserve(**accounting, call_id=call_id, model=self._config.model,
                                     system=context.system, prompt=prompt, schema=schema,
                                     max_output_tokens=output_cap, timeout_seconds=timeout,
                                     agent_limits=request["limits"])
                try:
                    result = await LLMAdapter(replace(self._config, max_output_tokens=output_cap,
                                                      timeout_seconds=timeout), transport=self._transport).generate(
                                                          system=context.system, prompt=prompt, schema=schema)
                except BaseException:
                    self._ledger.abandon(**accounting, call_id=call_id)
                    raise
                self._ledger.finish(**accounting, call_id=call_id, result=result)
                if not result.ok:
                    raise GenerationError(result.error.code, result.error.retryable)
                _require(Draft202012Validator(schema).is_valid(result.data), "GENERATION_INVALID_RESPONSE")
                contents = result.data["files"]
                _require(len({f["path"] for f in contents}) == len(batch)
                         and {f["path"] for f in contents} == {s.path for s in batch}, "GENERATION_INCOMPLETE_BATCH")
                by_path = {f["path"]: f["content"] for f in contents}
                artifacts = []
                for spec in batch:
                    content = by_path[spec.path]
                    _require(len(content.encode("utf-8")) <= limits["artifacts"]["max_file_bytes"], "GENERATION_FILE_LIMIT")
                    artifacts.append(self._artifact(spec, content, attempt_id, task))
                # Every validator sees all candidates as well as previously accepted files.
                candidates = tuple(accepted + artifacts)
                for spec in batch:
                    self._validate_file(spec, by_path[spec.path], context, deepcopy(candidates))
                _require(sum(a["size_bytes"] for a in accepted + artifacts) <= limits["artifacts"]["max_total_bytes"],
                         "GENERATION_TOTAL_SIZE_LIMIT")
                receipt = {"schema_version": "1.0", "fingerprint": batch_fingerprint,
                           "call_id": call_id, "artifacts": artifacts}
                self._store.accept(**scope, batch_id=batch_id, receipt=receipt)
                accepted.extend(artifacts)
            except asyncio.CancelledError:
                raise
            except GenerationError as exc:
                diagnostic, retryable = exc.code, exc.retryable
                break
            except Exception:
                # A store/ledger/subject failure is never turned into model success.
                diagnostic = "GENERATION_ACCEPTANCE_FAILED"
                break
        remaining = tuple(spec.path for spec in files if spec.path not in {a["path"] for a in accepted})
        if (remaining or not set(task["output_paths"]) <= {a["path"] for a in accepted}) and diagnostic is None:
            diagnostic = "GENERATION_INCOMPLETE_OUTPUT"
        usage = self._ledger.usage(**accounting)
        result = {"schema_version": "1.0", **{key: request[key] for key in ("run_id", "stage_id", "attempt", "contract_version")},
                  "status": "failed" if diagnostic else "succeeded", "output_artifacts": accepted,
                  "questions": [], "assumptions": [], "usage": usage,
                  "diagnostics": [] if diagnostic is None else [{"code": diagnostic,
                      "message": "Generation stopped; remaining files: " + _json(remaining),
                      "severity": "error", "retryable": retryable, "related_fr_ids": task["related_fr_ids"]}]}
        self._contracts.validate("agent-result", result)
        return GenerationOutcome(result, remaining)

    def _partition(self, files: tuple[FileSpec, ...], cap: int, context: PreparedContext, task: dict) -> tuple[tuple[FileSpec, ...], ...]:
        batches, current, cost = [], [], 256
        for spec in files:
            _require(spec.output_token_budget + 256 <= cap, "GENERATION_FILE_EXCEEDS_CALL_BUDGET")
            def size(candidate):
                schema = self._schema(tuple(candidate))
                prompt = self._prompt(context, tuple(candidate), [], task)
                config = replace(self._config, max_output_tokens=sum(s.output_token_budget for s in candidate) + 256)
                return self._request_size(config, context.system, prompt, schema)
            _require(size([spec]) <= self._request_bytes, "GENERATION_CONTEXT_LIMIT")
            if current and (len(current) >= self._batch_size or cost + spec.output_token_budget > cap
                            or size(current + [spec]) > self._request_bytes):
                batches.append(tuple(current)); current, cost = [], 256
            current.append(spec); cost += spec.output_token_budget
        if current:
            batches.append(tuple(current))
        return tuple(batches)

    @staticmethod
    def _request_size(config: LLMConfig, system: str, prompt: str, schema: dict) -> int:
        payload = request_payload(config, system, prompt, schema)
        return len(httpx.Request("POST", "https://example.invalid", json=payload).content)

    @staticmethod
    def _prompt(context: PreparedContext, batch: tuple[FileSpec, ...], accepted: list[dict], task: dict) -> str:
        return _json({"context": json.loads(context.data), "task": task, "batch_paths": [s.path for s in batch],
                      "accepted_files": accepted,
                      "instruction": "Return the complete contents of exactly these files. No patches or truncation."})

    @staticmethod
    def _schema(batch: tuple[FileSpec, ...]) -> dict:
        return {"type": "object", "additionalProperties": False, "required": ["files"],
                "properties": {"files": {"type": "array", "minItems": len(batch), "maxItems": len(batch),
                    "items": {"type": "object", "additionalProperties": False, "required": ["path", "content", "complete"],
                              "properties": {"path": {"enum": [s.path for s in batch]},
                                             "content": {"type": "string"}, "complete": {"const": True}}}}}}

    def _artifact(self, spec: FileSpec, content: str, attempt_id: str, task: dict, *, validate_content: bool = True) -> dict:
        raw = content.encode("utf-8")
        artifact = {"artifact_id": str(uuid5(UUID(attempt_id), f"{task['task_id']}:{spec.path}:{spec.version}")),
                    "attempt_id": attempt_id, "path": spec.path, "kind": spec.kind, "media_type": spec.media_type,
                    "version": spec.version, "schema_version": "1.0", "author_agent": task["owner_agent"],
                    "checksum": sha256(raw).hexdigest(), "size_bytes": len(raw),
                    "related_fr_ids": task["related_fr_ids"], "content": content}
        if validate_content:
            self._contracts.validate("artifact", artifact)
        else:
            common = self._contracts.schemas["common"]
            validator = Draft202012Validator({"$id": common["$id"], "$defs": common["$defs"],
                                             **common["$defs"]["artifact_reference"]},
                                            registry=self._contracts.registry)
            _require(validator.is_valid({k: v for k, v in artifact.items() if k != "content"}),
                     "GENERATION_INVALID_FILE_SPEC")
        return artifact

    def _verify_receipt(self, receipt: dict, fingerprint: str, batch: tuple[FileSpec, ...],
                        attempt_id: str, task: dict, usage: dict) -> list[dict]:
        _require(receipt.get("schema_version") == "1.0" and receipt.get("fingerprint") == fingerprint,
                 "GENERATION_RECEIPT_CONFLICT")
        call_id = receipt.get("call_id")
        try:
            _require(isinstance(call_id, str) and str(UUID(call_id)) == call_id, "GENERATION_RECEIPT_CONFLICT")
        except (ValueError, TypeError, AttributeError):
            raise GenerationError("GENERATION_RECEIPT_CONFLICT") from None
        _require(any(call.get("call_id") == call_id and call.get("outcome") == "succeeded"
                     for call in usage["model_calls"]), "GENERATION_RECEIPT_CONFLICT")
        artifacts = receipt["artifacts"]
        _require(len(artifacts) == len(batch) and len({a["path"] for a in artifacts}) == len(batch),
                 "GENERATION_RECEIPT_CONFLICT")
        by_path = {a["path"]: a for a in artifacts}
        for spec in batch:
            artifact = by_path.get(spec.path)
            _require(artifact is not None and artifact == self._artifact(spec, artifact["content"], attempt_id, task),
                     "GENERATION_RECEIPT_CONFLICT")
        return [by_path[spec.path] for spec in batch]
