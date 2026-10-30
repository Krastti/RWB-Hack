"""Injected LLM port and module-local CLI transport, pending shared A-01 (#75)."""

import json
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def load_json(value: str):
    """Reject duplicate fields and non-JSON numeric constants instead of guessing."""
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def invalid_constant(_):
        raise ValueError("non-JSON numeric constant")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid_constant)


@dataclass(frozen=True)
class Completion:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMError(Exception):
    """Public codes only; no provider response, URL or credentials in exceptions."""

    def __init__(self, code: str, *, retryable: bool = False, completion: Completion | None = None):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.completion = completion


class LLMClient(Protocol):
    def complete(self, *, messages: list[dict[str, str]], schema: dict,
                 max_output_tokens: int, timeout_seconds: float) -> Completion: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAICompatibleClient:
    """Explicit compatibility mode; no assumption about the contest API protocol."""

    def __init__(self, *, base_url: str, model: str, api_key: str,
                 response_format: str = "json_schema"):
        url = urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.query or url.fragment or not model.strip()
                or response_format not in {"json_schema", "json_object"}):
            raise ValueError("invalid LLM configuration")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        self._format = response_format

    def complete(self, *, messages, schema, max_output_tokens, timeout_seconds):
        response_format = {"type": self._format}
        if self._format == "json_schema":
            response_format["json_schema"] = {
                "name": "requirements_analysis", "strict": True, "schema": schema,
            }
        else:
            # JSON object mode still receives the full schema, then local validation.
            messages = [*messages, {"role": "user", "content": "JSON Schema:\n" + json.dumps(schema)}]
        payload = {
            "model": self._model, "messages": messages, "temperature": 0,
            "max_tokens": max_output_tokens, "response_format": response_format,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        request = Request(self._url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            # Never forward credentials through a server redirect.
            with build_opener(NoRedirect()).open(request, timeout=timeout_seconds) as response:
                body = response.read(8_000_001)
                if len(body) > 8_000_000:
                    raise LLMError("LLM_RESPONSE_TOO_LARGE")
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {401, 403}:
                raise LLMError("LLM_AUTHENTICATION") from None
            raise LLMError("LLM_HTTP_ERROR", retryable=code == 429 or code >= 500) from None
        except (TimeoutError, socket.timeout):
            raise LLMError("LLM_TIMEOUT", retryable=True) from None
        except URLError as exc:
            code = "LLM_TIMEOUT" if isinstance(exc.reason, TimeoutError) else "LLM_UNAVAILABLE"
            raise LLMError(code, retryable=True) from None
        except OSError:
            raise LLMError("LLM_UNAVAILABLE", retryable=True) from None
        try:
            raw = load_json(body.decode("utf-8"))
            choice = raw["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("missing content")
            usage = raw.get("usage") or {}
            tokens = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
            if any(t is not None and (type(t) is not int or t < 0) for t in tokens):
                raise ValueError("invalid usage")
            completion = Completion(content, *tokens)
            if choice.get("finish_reason") != "stop":
                raise LLMError("LLM_INCOMPLETE_RESPONSE", completion=completion)
            return completion
        except (AttributeError, KeyError, IndexError, TypeError, ValueError, UnicodeError, RecursionError):
            raise LLMError("LLM_INVALID_ENVELOPE") from None
