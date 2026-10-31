"""Structured, non-streaming Chat Completions calls without automatic retries."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from jsonschema import FormatChecker
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource

from ...config.llm import LLMConfig
from ...models.dto.llm import LLMResult, LLMUsage
from ...errors.llm import LLMError


def _no_remote_schema(uri: str) -> Any:
    # Schemas must be self-contained; validation must not perform network I/O.
    raise NoSuchResource(ref=uri)


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-JSON numeric constant")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _json_loads(value: str) -> Any:
    return json.loads(value, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def _contains_secret(value: Any, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(_contains_secret(key, secret) or _contains_secret(item, secret) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item, secret) for item in value)
    return False


def provider_shape(schema):
    """Decoder guidance only; the untouched full schema remains authoritative."""
    if isinstance(schema, list):
        return [provider_shape(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key in {'if', 'then', 'else', 'not', 'contains', 'minContains', 'maxContains',
                   'uniqueItems', 'propertyNames', 'unevaluatedProperties', 'dependentSchemas'}:
            continue
        if key in {'properties', '$defs', 'definitions', 'patternProperties'}:
            result[key] = {name: provider_shape(child) for name, child in value.items()}
        elif key in {'const', 'enum', 'default', 'examples'}:
            result[key] = value
        elif key == 'pattern':
            pattern = value[:value.index('$(?!')] + '$' if '$(?!' in value else value
            if '(?' not in pattern:
                result[key] = pattern
        elif key == 'allOf':
            parts = [provider_shape(v) for v in value]
            parts = [v for v in parts if v]
            if parts:
                result[key] = parts
        else:
            result[key] = provider_shape(value)
    return result


def request_payload(config: LLMConfig, system: str, prompt: str, schema: dict) -> dict:
    """Build the exact JSON envelope dispatched by the adapter."""
    compat = config.response_format == "json_schema_compat"
    response_format: dict[str, Any] = {"type": "json_schema" if compat else config.response_format}
    if config.response_format in {"json_schema", "json_schema_compat"}:
        response_format["json_schema"] = {"name": "result", "strict": True, "schema": provider_shape(schema) if compat else schema}
    instruction = system + "\nReturn only JSON matching the supplied response schema."
    if config.response_format in {"json_object", "json_schema_compat"}:
        instruction += "\n" + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    payload = {"model": config.model,
            "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": prompt}],
            "response_format": response_format, "max_tokens": config.max_output_tokens, "stream": False}
    if config.enable_thinking is not None:
        payload["chat_template_kwargs"] = {"enable_thinking": config.enable_thinking}
    return payload


class LLMAdapter:
    """One HTTP call per generate(); caller owns retries and run budgets.

    An optional transport is for local in-memory verification only. Each call
    owns and closes its client, and cancellation propagates to the caller.
    """

    def __init__(self, config: LLMConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._transport = transport

    async def generate(self, *, system: str, prompt: str, schema: dict[str, Any]) -> LLMResult:
        from backend.app.agents.llm.accounting import active_accounting
        accounting = active_accounting.get()
        if accounting is not None:
            return await accounting.generate(self, system, prompt, schema)
        return await self._generate(system=system, prompt=prompt, schema=schema)

    async def _generate(self, *, system: str, prompt: str, schema: dict[str, Any]) -> LLMResult:
        start = time.monotonic()
        calls = 0
        counts: dict[str, int | None] = {}

        def usage() -> LLMUsage:
            return LLMUsage(**counts, calls=calls, latency_ms=round((time.monotonic() - start) * 1000))

        def fail(code: str, message: str, retryable: bool = False, status: int | None = None) -> LLMResult:
            return LLMResult(usage=usage(), error=LLMError(code, message, retryable, status))

        try:
            if not isinstance(system, str) or not isinstance(prompt, str) or not prompt.strip():
                return fail("LLM_INVALID_INPUT", "A nonempty prompt and string system message are required.")
            if not isinstance(schema, dict):
                return fail("LLM_INVALID_SCHEMA", "A JSON Schema object is required.")
            # Copy the caller's schema and reject values outside the JSON data model.
            schema = _json_loads(json.dumps(schema, allow_nan=False))
            validator_class = validator_for(schema)
            validator_class.check_schema(schema)
            validator = validator_class(
                schema, registry=Registry(retrieve=_no_remote_schema), format_checker=FormatChecker(),
            )
            payload = request_payload(self._config, system, prompt, schema)
        except Exception:
            # Never expose validation exceptions: they can contain schema values.
            return fail("LLM_INVALID_SCHEMA", "The supplied schema cannot be used.")

        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=self._config.timeout_seconds,
                    follow_redirects=False, trust_env=False,
                ) as client:
                    calls = 1
                    response = await client.post(
                        self._config.base_url.rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + self._config.api_key}, json=payload,
                    )
        except (TimeoutError, httpx.TimeoutException):
            return fail("LLM_TIMEOUT", "The model call timed out.", True)
        except httpx.RequestError:
            return fail("LLM_UNAVAILABLE", "The model API is unavailable.", True)

        status = response.status_code
        # A definitive client rejection did not admit a completion. Keep the
        # physical request in the ledger, but release its token reservation.
        # Timeouts/5xx remain unknown and retain conservative reservations.
        if status in {400, 401, 403, 404, 413, 415, 422, 429}:
            counts.update(tokens_in=0, tokens_out=0, total_tokens=0)
        if status >= 400:
            try:
                reported = _json_loads(response.text).get("usage")
                if isinstance(reported, dict):
                    for source, target in (("prompt_tokens", "tokens_in"), ("completion_tokens", "tokens_out"), ("total_tokens", "total_tokens")):
                        value = reported.get(source)
                        counts[target] = value if type(value) is int and value >= 0 else None
            except (ValueError, TypeError, AttributeError):
                pass
        if status in (401, 403):
            return fail("LLM_AUTH_ERROR", "The model API rejected the credentials.", status=status)
        if status == 429:
            return fail("LLM_RATE_LIMITED", "The model API rate limit was reached.", True, status)
        if status in (408, 504):
            return fail("LLM_TIMEOUT", "The model API timed out.", True, status)
        if status >= 500:
            return fail("LLM_UNAVAILABLE", "The model API is unavailable.", True, status)
        if not 200 <= status < 300:
            return fail("LLM_REQUEST_REJECTED", "The model API rejected the request.", status=status)

        try:
            body = _json_loads(response.text)
            raw_usage = body.get("usage")
            if raw_usage is not None:
                for source, target in (("prompt_tokens", "tokens_in"), ("completion_tokens", "tokens_out"), ("total_tokens", "total_tokens")):
                    count = raw_usage.get(source)
                    if count is not None and (type(count) is not int or count < 0):
                        raise ValueError("Invalid usage")
                    counts[target] = count
            choices = body["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("Expected one choice")
            choice = choices[0]
            message = choice["message"]
            if message.get("refusal"):
                return fail("LLM_REFUSED", "The model refused the request.")
            if choice.get("finish_reason") == "length":
                return fail("LLM_OUTPUT_TRUNCATED", "The model output token limit was reached.")
            if choice.get("finish_reason") != "stop":
                return fail("LLM_INVALID_RESPONSE", "The model did not return a complete JSON response.")
            content = message["content"]
            if not isinstance(content, str):
                raise ValueError("Expected JSON text")
            data = _json_loads(content)
            # Do not return an accidentally echoed credential, including escapes.
            json.dumps(data, allow_nan=False)
            if _contains_secret(data, self._config.api_key):
                return fail("LLM_INVALID_RESPONSE", "The model returned unsafe content.")
            errors = list(validator.iter_errors(data))
            if errors:
                feedback = tuple("/" + "/".join(map(str, error.absolute_path)) + ": " + error.validator
                                 for error in errors[:20])
                return LLMResult(usage=usage(), error=LLMError("LLM_INVALID_RESPONSE",
                    "The model response is not valid for the requested schema."),
                    candidate=data, validation_feedback=feedback)
            return LLMResult(data=data, usage=usage())
        except Exception:
            # No provider body, prompt, content, URL or exception text in errors.
            return fail("LLM_INVALID_RESPONSE", "The model response is not valid for the requested schema.")
