import json

import pytest

import llm
from common import load_schema


def test_gemini_schema_strips_unsupported_keywords_but_keeps_structure():
    s = llm._to_gemini_schema(load_schema("deploy_plan"))
    flat = json.dumps(s)
    assert "additionalProperties" not in flat and "maxLength" not in flat
    assert s["properties"]["cloud"]["enum"] == ["aws", "gcp"] and "required" in s


def test_mock_mode_validates_against_schema(tmp_path, monkeypatch):
    good = tmp_path / "g.json"
    good.write_text(json.dumps({"risk": "low", "summary": "s", "concerns": [], "recommendation": "approve"}))
    monkeypatch.setenv("AI_MOCK_RESPONSE", str(good))
    assert llm.generate_json("x", load_schema("plan_review"))["risk"] == "low"

    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"risk": "catastrophic"}))
    monkeypatch.setenv("AI_MOCK_RESPONSE", str(bad))
    with pytest.raises(llm.LLMError):
        llm.generate_json("x", load_schema("plan_review"))


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("AI_MOCK_RESPONSE", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(llm.LLMError):
        llm.generate_json("x", load_schema("plan_review"))


def test_invalid_model_output_gets_one_repair_attempt(monkeypatch):
    monkeypatch.delenv("AI_MOCK_RESPONSE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    replies = iter(["not json", json.dumps({"risk": "low", "summary": "s", "concerns": [], "recommendation": "approve"})])
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {"candidates": [{"content": {"parts": [{"text": next(replies)}]}}]})
    assert llm.generate_json("x", load_schema("plan_review"))["risk"] == "low"


def test_model_chain_falls_through_on_unavailable(monkeypatch):
    monkeypatch.delenv("AI_MOCK_RESPONSE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "busy-model,good-model")
    ok = json.dumps({"risk": "low", "summary": "s", "concerns": [], "recommendation": "approve"})

    def fake_post(url, *a, **k):
        if "busy-model" in url:
            raise llm.LLMError("Gemini HTTP 503")
        return {"candidates": [{"content": {"parts": [{"thought": True, "text": "thinking..."}, {"text": ok}]}}]}

    monkeypatch.setattr(llm, "_post", fake_post)
    assert llm.generate_json("x", load_schema("plan_review"))["risk"] == "low"


def test_all_models_failing_raises(monkeypatch):
    monkeypatch.delenv("AI_MOCK_RESPONSE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "a,b")
    monkeypatch.setattr(llm, "_post", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("down")))
    with pytest.raises(llm.LLMError):
        llm.generate_json("x", load_schema("plan_review"))
