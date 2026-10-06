import json
from pathlib import Path

import health_judge as hj

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def llm(healthy, conf):
    return lambda *a, **k: {"healthy": healthy, "confidence": conf, "summary": "s", "evidence": [], "suspected_cause": "none"}


def down(*a, **k):
    raise hj.LLMError("api down")


def test_healthy_path():
    assert hj.decide(load("healthy.json"), llm(True, 0.95))["outcome"] == "healthy"


def test_hard_gate_cannot_be_overridden_by_llm():
    # Model claims everything is fine, but the pod is crash-looping => must still be unhealthy
    r = hj.decide(load("crashloop.json"), llm(True, 0.99))
    assert r["outcome"] == "unhealthy" and any("CrashLoopBackOff" in g for g in r["gate_failures"])


def test_llm_outage_does_not_roll_back_a_good_deploy():
    r = hj.decide(load("healthy.json"), down)
    assert r["outcome"] == "healthy" and r["ai_error"]


def test_llm_outage_still_catches_hard_failures():
    assert hj.decide(load("crashloop.json"), down)["outcome"] == "unhealthy"


def test_confident_ai_problem_triggers_rollback():
    assert hj.decide(load("subtle_errors.json"), llm(False, 0.9))["outcome"] == "unhealthy"


def test_unsure_ai_means_human_review_not_rollback():
    assert hj.decide(load("subtle_errors.json"), llm(False, 0.6))["outcome"] == "uncertain"
    assert hj.decide(load("healthy.json"), llm(True, 0.3))["outcome"] == "uncertain"


def test_redaction():
    line = "connect postgresql://ideas:S3cr3tPass@10.0.0.5:5432/ideas failed password=hunter2"
    out = hj.redact(line)
    assert "S3cr3tPass" not in out and "hunter2" not in out


def test_restart_and_oom_gates():
    ev = load("healthy.json")
    ev["pods"][0]["restarts"] = 4
    ev["pods"][1]["last_terminated_reason"] = "OOMKilled"
    assert len(hj.hard_gates(ev)) == 2
