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


def test_smoke_tolerates_cold_start_then_passes(monkeypatch):
    """First requests are slow/timeouts (new LB); once warm, all samples succeed => healthy, not a failure."""
    calls = {"n": 0}

    def fake_get(url, timeout=10):
        calls["n"] += 1
        if calls["n"] <= 4:          # cold: timeouts
            return 0, b"", 15000.0
        return 200, b"[]", 40.0

    monkeypatch.setattr(hj, "http_get", fake_get)
    monkeypatch.setattr(hj.time, "sleep", lambda *_: None)
    r = hj.smoke("http://x", wait_seconds=600)
    assert r["ok"] and r["warmed_up"] and r["max_ms"] == 40


def test_smoke_fails_if_never_warms_up(monkeypatch):
    monkeypatch.setattr(hj, "http_get", lambda url, timeout=10: (0, b"", 15000.0))
    monkeypatch.setattr(hj.time, "sleep", lambda *_: None)
    ticks = iter(range(0, 10_000, 1))
    monkeypatch.setattr(hj.time, "monotonic", lambda: next(ticks) * 10.0)
    r = hj.smoke("http://x", wait_seconds=60)
    assert not r["ok"] and not r["warmed_up"]
    assert any("smoke check failed" in g for g in hj.hard_gates({"pods": [{"name": "p", "ready": True, "restarts": 0, "waiting_reason": None, "last_terminated_reason": None, "phase": "Running"}], "smoke": r}))


def _warm_then(monkeypatch, failing_sample_numbers):
    """Warm-up (3 rounds, 2 calls each) succeeds; then the listed API sample numbers (1-based) return a timeout."""
    state = {"n": 0, "api_after_warm": 0}

    def fake_get(url, timeout=10):
        state["n"] += 1
        if state["n"] > 6 and "/api/ideas" in url:
            state["api_after_warm"] += 1
            if state["api_after_warm"] in failing_sample_numbers:
                return 0, b"", 15000.0
        return 200, b"[]", 20.0

    monkeypatch.setattr(hj, "http_get", fake_get)
    monkeypatch.setattr(hj.time, "sleep", lambda *_: None)


def test_one_transient_failed_sample_is_tolerated(monkeypatch):
    _warm_then(monkeypatch, {5})
    r = hj.smoke("http://x", wait_seconds=600)
    assert r["warmed_up"] and r["ok"]
    assert r["checks"][1]["failed_samples"] == 1 and "tolerated" in r["checks"][1]["note"]


def test_two_failed_samples_fail_the_gate(monkeypatch):
    _warm_then(monkeypatch, {3, 7})
    r = hj.smoke("http://x", wait_seconds=600)
    assert r["warmed_up"] and not r["ok"]


def test_scanner_noise_and_hpa_warmup_are_filtered_from_what_the_model_sees():
    ev = load("healthy.json")
    ev["logs"]["frontend"] = [
        '10.0.1.7 - - "GET /radio.php HTTP/1.1" 200 395',
        '10.0.1.7 - - "GET /wp-login.php HTTP/1.1" 200 395',
        '10.0.1.7 - - "GET /.env HTTP/1.1" 200 395',
        '10.0.1.7 - - "GET / HTTP/1.1" 200 615',
    ]
    ev["warning_events"] = [
        {"reason": "FailedGetResourceMetric", "message": "no metrics returned", "object": "hpa/ib-backend"},
        {"reason": "BackOff", "message": "Back-off restarting failed container", "object": "pod/x"},
    ]
    d = hj.llm_digest(ev)
    assert d["frontend_log_tail"] == ['10.0.1.7 - - "GET / HTTP/1.1" 200 615']
    assert d["filtered_as_background_noise"] == {"internet_scanner_requests": 3, "hpa_metrics_warmup_events": 1}
    assert [e["reason"] for e in d["warning_events"]] == ["BackOff"]   # real problems are NOT filtered


def test_real_errors_are_never_filtered_as_noise():
    ev = load("subtle_errors.json")
    d = hj.llm_digest(ev)
    assert d["backend_error_line_count"] == 3 and len(d["backend_log_tail"]) == 4
