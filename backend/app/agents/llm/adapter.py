"""Structured, non-streaming Chat Completions calls without automatic retries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import math
import time
from typing import Any, Literal

import httpx
from jsonschema import FormatChecker
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0
    max_output_tokens: int = 2048
    response_format: Literal["json_schema", "json_object"] = "json_schema"

    def __post_init__(self) -> None:
        try:
            url = httpx.URL(self.base_url)
            valid = (
                url.scheme in ("http", "https") and bool(url.host)
                and not url.userinfo and not url.query and not url.fragment
                and bool(self.model.strip()) and bool(self.api_key.strip())
                and self.api_key.isascii() and not any(ord(c) < 33 or ord(c) == 127 for c in self.api_key)
                and math.isfinite(self.timeout_seconds) and self.timeout_seconds > 0
                and type(self.max_output_tokens) is int and self.max_output_tokens > 0
                and self.response_format in ("json_schema", "json_object")
            )
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("Invalid LLM configuration") from None


@dataclass(frozen=True)
class LLMUsage:
    tokens_in: int | None = None
    tokens_out: int | None = None
    total_tokens: int | None = None
    latency_ms: int = 0
    calls: int = 0


@dataclass(frozen=True)
class LLMError:
    code: str
    message: str
    retryable: bool = False
    http_status: int | None = None


@dataclass(frozen=True)
class LLMResult:
    data: Any = field(default=None, repr=False)
    usage: LLMUsage = field(default_factory=LLMUsage)
    error: LLMError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


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


class LLMAdapter:
    """One HTTP call per generate(); caller owns retries and run budgets.

    An optional transport is for local in-memory verification only. Each call
    owns and closes its client, and cancellation propagates to the caller.
    """

    def __init__(self, config: LLMConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._transport = transport

    async def generate(self, *, system: str, prompt: str, schema: dict[str, Any]) -> LLMResult:
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
            response_format: dict[str, Any] = {"type": self._config.response_format}
            if self._config.response_format == "json_schema":
                response_format["json_schema"] = {"name": "result", "strict": True, "schema": schema}
            # JSON-object mode also receives the exact schema; local validation is
            # authoritative in either mode. No silent fallback or extra model call.
            instruction = system + "\nReturn only JSON matching this schema:\n" + json.dumps(schema)
            payload = {
                "model": self._config.model,
                "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": prompt}],
                "response_format": response_format,
                "max_tokens": self._config.max_output_tokens,
                "stream": False,
            }
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
            validator.validate(data)
            return LLMResult(data=data, usage=usage())
        except Exception:
            # No provider body, prompt, content, URL or exception text in errors.
            return fail("LLM_INVALID_RESPONSE", "The model response is not valid for the requested schema.")
