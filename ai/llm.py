"""Minimal Gemini client with schema-validated JSON output.

Design rules:
  * The model only ever returns DATA (JSON validated against a JSON Schema). Nothing it
    returns is executed; callers map validated fields to allow-listed actions.
  * Plain REST via urllib: no SDK, so the CI job needs only `pip install jsonschema`.
  * Set AI_MOCK_RESPONSE=/path/to/file.json to run every tool offline (demos, tests).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

import jsonschema

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Alias that tracks the current Flash model, then a lighter model as fallback when the first is
# overloaded (503) or retired (404). Pinned names get retired (gemini-2.5-flash already was for new
# users), so override with GEMINI_MODEL="a,b" only if you need reproducibility.
DEFAULT_MODEL = "gemini-3.1-flash-lite,gemini-flash-latest"  # comma-separated = fallback chain, fastest first

# Keywords Gemini's responseSchema (an OpenAPI subset) rejects. We still enforce them locally
# with jsonschema after the response arrives.
_UNSUPPORTED = {"$schema", "$id", "title", "additionalProperties", "pattern", "minLength", "maxLength", "default"}


class LLMError(RuntimeError):
    """The model was unreachable or never produced schema-valid output."""


def _to_gemini_schema(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _to_gemini_schema(v) for k, v in node.items() if k not in _UNSUPPORTED}
    if isinstance(node, list):
        return [_to_gemini_schema(v) for v in node]
    return node


def _post(url: str, body: dict, key: str, timeout: int = 40, retries: int = 2) -> dict:
    """POST with a short timeout. Only HTTP 429/5xx are retried (once); a timeout or connection error
    fails immediately so the caller can move on to the next model instead of stalling the pipeline."""
    data = json.dumps(body).encode()
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json", "x-goog-api-key": key}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            retryable = e.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == retries:
                raise LLMError(f"Gemini HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"Gemini unreachable or too slow (>{timeout}s): {e}") from e
        time.sleep(2 ** attempt)
    raise LLMError("unreachable")  # pragma: no cover


def generate_json(prompt: str, schema: dict, system: str = "", model: str | None = None) -> dict:
    """Return a dict that is guaranteed to validate against `schema`, or raise LLMError."""
    mock = os.environ.get("AI_MOCK_RESPONSE")
    if mock:
        with open(mock) as f:
            result = json.load(f)
        _validate(result, schema)
        return result

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("GEMINI_API_KEY is not set")
    chain = [m.strip() for m in (model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL).split(",") if m.strip()]

    errors = []
    for m in chain:
        try:
            return _generate_with(m, prompt, schema, system, key)
        except LLMError as e:  # unavailable / retired / invalid twice: try the next model
            errors.append(f"{m}: {e}")
    raise LLMError(" | ".join(errors))


def _generate_with(model: str, prompt: str, schema: dict, system: str, key: str) -> dict:
    body = {
        "systemInstruction": {"parts": [{"text": system}]} if system else None,
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": _to_gemini_schema(schema),
        },
    }
    body = {k: v for k, v in body.items() if v is not None}

    last_err = ""
    for attempt in range(2):  # one repair attempt if the output is invalid
        contents = body["contents"]
        if last_err:
            contents = contents + [
                {"role": "user", "parts": [{"text": f"Your previous reply was invalid: {last_err}. Reply again with valid JSON only."}]}
            ]
        resp = _post(API.format(model=model), {**body, "contents": contents}, key)
        try:
            parts = resp["candidates"][0]["content"]["parts"]
            # Thinking models may add non-answer parts; keep only answer text
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            result = json.loads(text)
            _validate(result, schema)
            return result
        except (KeyError, IndexError, json.JSONDecodeError, jsonschema.ValidationError) as e:
            last_err = str(e)[:300]
    raise LLMError(f"model returned invalid output twice: {last_err}")


def _validate(result: Any, schema: dict) -> None:
    try:
        jsonschema.validate(result, schema)
    except jsonschema.ValidationError as e:
        raise LLMError(f"output failed schema validation: {e.message}") from e
