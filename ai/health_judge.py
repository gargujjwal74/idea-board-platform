#!/usr/bin/env python3
"""Post-deployment health judgement with automatic-rollback signalling.

Layer 1 (deterministic, authoritative): pods ready, no crash loops / OOM / image pull errors,
  restarts, end-to-end HTTP smoke test. Any failure => UNHEALTHY. The LLM can NOT override this.
Layer 2 (LLM): reads logs/events/latency for subtler problems (error bursts, DB trouble, latency).

Exit codes:  0 healthy   1 unhealthy -> pipeline rolls back   2 uncertain -> pipeline keeps release, asks a human
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

from common import load_prompt, load_schema, set_outputs
from llm import LLMError, generate_json

BAD_WAITING = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "RunContainerError"}
ERR_RE = re.compile(r"\b(ERROR|CRITICAL|FATAL|Traceback|Exception)\b|\" 5\d\d ")
SECRET_RE = re.compile(r"(://)[^:@/\s]+:[^@/\s]+@|((?:password|token|secret|api[_-]?key)\s*[=:]\s*)\S+", re.I)
# Background noise that is NOT a signal about the release:
#  * internet scanners probe every public IP within hours (.php, wp-*, .env, ...); the SPA fallback answers
#    200 to unknown paths, so these lines look "successful" but mean nothing about health;
#  * HPA metrics and probe failures during the first minutes after a rollout are warm-up, not failure.
SCANNER_RE = re.compile(
    r"(\.php\b|/wp-(admin|login|content|includes)|/\.env|/\.git|phpmyadmin|/cgi-bin|xmlrpc|/boaform|/HNAP1|"
    r"/actuator|/vendor/phpunit|/\.aws|/server-status|/owa/|/solr/|/manager/html|/ecp/)", re.I)
BENIGN_EVENT_REASONS = {"FailedGetResourceMetric", "FailedComputeMetricsReplicas"}


def drop_scanner_noise(lines: list[str]) -> tuple[list[str], int]:
    kept = [l for l in lines if not SCANNER_RE.search(l)]
    return kept, len(lines) - len(kept)


RESTART_LIMIT = 3
ROLLBACK_CONFIDENCE = 0.8
HEALTHY_CONFIDENCE = 0.5


def redact(line: str) -> str:
    return SECRET_RE.sub(lambda m: (m.group(1) + "***:***@") if m.group(1) else (m.group(2) + "***"), line)


# ----------------------------------------------------------------------------- collection
def sh(*cmd: str, timeout: int = 60) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


def collect_pods(ns: str, release: str) -> list[dict]:
    raw = sh("kubectl", "-n", ns, "get", "pods", "-l", f"app.kubernetes.io/instance={release}", "-o", "json")
    pods = []
    for p in (json.loads(raw).get("items", []) if raw else []):
        cs = (p["status"].get("containerStatuses") or [{}])[0]
        waiting = (cs.get("state", {}).get("waiting") or {}).get("reason")
        last_term = (cs.get("lastState", {}).get("terminated") or {}).get("reason")
        pods.append({"name": p["metadata"]["name"], "phase": p["status"].get("phase"),
                     "ready": bool(cs.get("ready")), "restarts": cs.get("restartCount", 0),
                     "waiting_reason": waiting, "last_terminated_reason": last_term})
    return pods


def collect_events(ns: str) -> list[dict]:
    raw = sh("kubectl", "-n", ns, "get", "events", "--field-selector", "type=Warning", "-o", "json")
    items = json.loads(raw).get("items", []) if raw else []
    return [{"reason": e.get("reason"), "message": redact(e.get("message", ""))[:200],
             "object": e.get("involvedObject", {}).get("name")} for e in items[-15:]]


def collect_logs(ns: str, release: str) -> dict[str, list[str]]:
    out = {}
    for comp in ("backend", "frontend"):
        txt = sh("kubectl", "-n", ns, "logs", f"deploy/{release}-{comp}", "--tail=150", "--since=15m")
        out[comp] = [redact(l)[:300] for l in txt.splitlines()]
    return out


def http_get(url: str, timeout: int = 10) -> tuple[int, bytes, float]:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), (time.monotonic() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, b"", (time.monotonic() - t0) * 1000
    except Exception:
        return 0, b"", (time.monotonic() - t0) * 1000


def _api_ok(base: str) -> tuple[int, float, bool]:
    s, body, ms = http_get(base + "/api/ideas", timeout=15)
    try:
        return s, ms, s == 200 and isinstance(json.loads(body), list)
    except Exception:
        return s, ms, False


def smoke(base: str, wait_seconds: int, warm_needed: int = 3, samples: int = 10, max_failed_samples: int = 1) -> dict:
    """End-to-end: browser entry point + API through the nginx proxy + DB. GET-only (no writes to real data).

    Two phases, because a brand-new cloud load balancer and a freshly started backend are slow on the
    FIRST requests (observed: ~6 s) and that must not be mistaken for a broken release:
      1. warm-up: keep probing until `warm_needed` consecutive successes (or the wait window ends);
      2. measure: `samples` requests; at most `max_failed_samples` may fail (one stalled connection through a
         cloud load balancer is not a broken release, while a real outage fails them all). Every status and the
         latency are reported so the AI can judge the rest.
    """
    base = base.rstrip("/")
    deadline = time.monotonic() + wait_seconds
    streak, root_status = 0, 0
    while time.monotonic() < deadline and streak < warm_needed:
        root_status, _, _ = http_get(base + "/", timeout=15)
        _, _, api_ok = _api_ok(base)
        streak = streak + 1 if (root_status == 200 and api_ok) else 0
        if streak < warm_needed:
            time.sleep(5)
    warmed = streak >= warm_needed

    statuses, lat, failed = [], [], 0
    for _ in range(samples if warmed else 1):
        s, ms, ok = _api_ok(base)
        statuses.append(s)
        lat.append(ms)
        failed += 0 if ok else 1
    all_ok = warmed and failed <= max_failed_samples
    checks = [
        {"name": "GET /", "ok": root_status == 200, "status": root_status, "statuses": [root_status]},
        {"name": f"GET /api/ideas x{samples} (after warm-up)", "ok": all_ok, "status": statuses[-1], "statuses": statuses,
         "failed_samples": failed,
         "note": f"never reached {warm_needed} consecutive successes within {wait_seconds}s (status 0 = timeout/connection error)" if not warmed else (f"{failed} transient failed sample(s) tolerated" if failed else "")},
    ]
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "p50_ms": round(statistics.median(lat)), "max_ms": round(max(lat)), "warmed_up": warmed}


# ----------------------------------------------------------------------------- judgement
def hard_gates(ev: dict) -> list[str]:
    fails = []
    pods = ev["pods"]
    if not pods:
        fails.append("no pods found for this release")
    for p in pods:
        if not p["ready"]:
            fails.append(f"pod {p['name']} not ready (phase={p['phase']}, waiting={p['waiting_reason']})")
        if p["waiting_reason"] in BAD_WAITING:
            fails.append(f"pod {p['name']} is {p['waiting_reason']}")
        if p["last_terminated_reason"] == "OOMKilled":
            fails.append(f"pod {p['name']} was OOMKilled")
        if p["restarts"] >= RESTART_LIMIT:
            fails.append(f"pod {p['name']} restarted {p['restarts']} times")
    for c in ev["smoke"]["checks"]:
        if not c["ok"]:
            detail = ",".join(str(x) for x in c.get("statuses", [c["status"]]))
            fails.append(f"smoke check failed: {c['name']} (statuses: {detail}; 0 = timeout/connection error) {c.get('note', '')}".strip())
    return fails


def llm_digest(ev: dict) -> dict:
    be, be_noise = drop_scanner_noise(ev["logs"].get("backend", []))
    fe, fe_noise = drop_scanner_noise(ev["logs"].get("frontend", []))
    events = [e for e in ev["warning_events"] if e.get("reason") not in BENIGN_EVENT_REASONS]
    return {
        "pods": ev["pods"],
        "warning_events": events,
        "smoke": {"p50_ms": ev["smoke"]["p50_ms"], "max_ms": ev["smoke"]["max_ms"], "checks": ev["smoke"]["checks"]},
        "backend_error_line_count": sum(1 for l in be if ERR_RE.search(l)),
        "backend_log_tail": be[-60:],
        "frontend_log_tail": fe[-20:],
        "filtered_as_background_noise": {
            "internet_scanner_requests": be_noise + fe_noise,
            "hpa_metrics_warmup_events": len(ev["warning_events"]) - len(events),
        },
    }


def decide(ev: dict, ask_llm=generate_json) -> dict:
    gates = hard_gates(ev)
    verdict_ai, ai_error = None, None
    try:
        verdict_ai = ask_llm("Deployment evidence:\n" + json.dumps(llm_digest(ev), indent=2),
                             load_schema("health_verdict"), system=load_prompt("health_judge"))
    except LLMError as e:
        ai_error = str(e)

    if gates:  # authoritative; the model only explains
        outcome, reason = "unhealthy", "Deterministic gate(s) failed: " + "; ".join(gates)
    elif verdict_ai is None:
        outcome, reason = "healthy", f"All deterministic gates passed (AI analysis unavailable: {ai_error})"
    elif not verdict_ai["healthy"] and verdict_ai["confidence"] >= ROLLBACK_CONFIDENCE:
        outcome, reason = "unhealthy", f"AI detected a problem with confidence {verdict_ai['confidence']:.2f}"
    elif not verdict_ai["healthy"] or verdict_ai["confidence"] < HEALTHY_CONFIDENCE:
        outcome, reason = "uncertain", f"AI is unsure (healthy={verdict_ai['healthy']}, confidence={verdict_ai['confidence']:.2f}); human review needed"
    else:
        outcome, reason = "healthy", "Gates passed and AI found no problems"
    return {"outcome": outcome, "reason": reason, "gate_failures": gates, "ai": verdict_ai, "ai_error": ai_error}


def to_markdown(res: dict, ev: dict, meta: dict) -> str:
    icon = {"healthy": "✅", "unhealthy": "❌", "uncertain": "⚠️"}[res["outcome"]]
    ai = res["ai"]
    lines = [f"### {icon} Post-deploy health: **{res['outcome']}** ({meta['cloud']} / `{meta['release']}`)", "", res["reason"], ""]
    if ai:
        label = "AI analysis (advisory only: deterministic gates take precedence)" if res["gate_failures"] else "AI analysis"
        lines += [f"**{label}** (confidence {ai['confidence']:.2f}): {ai['summary']}"]
        if ai["suspected_cause"] and ai["suspected_cause"].lower() != "none":
            lines += [f"- Suspected cause: {ai['suspected_cause']}"]
        lines += [f"- Evidence: `{e}`" for e in ai["evidence"]]
        lines += [""]
    s = ev["smoke"]
    lines += [f"Smoke: p50 {s['p50_ms']} ms, max {s['max_ms']} ms; pods: "
              + ", ".join(f"{p['name']} ready={p['ready']} restarts={p['restarts']}" for p in ev["pods"])]
    if res["outcome"] == "unhealthy":
        lines += ["", "**Action:** automatic rollback to the previous release."]
    if res["outcome"] == "uncertain":
        lines += ["", "**Action:** release kept in place; please review before promoting."]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--release", required=True)
    ap.add_argument("--url", help="public base URL, e.g. http://my-lb-hostname")
    ap.add_argument("--cloud", default="unknown")
    ap.add_argument("--wait-seconds", type=int, default=300)
    ap.add_argument("--snapshot", help="use a saved evidence JSON instead of kubectl/HTTP (tests, demos)")
    ap.add_argument("--out", default="health-report")
    a = ap.parse_args()

    if a.snapshot:
        ev = json.load(open(a.snapshot))
    else:
        if not a.url:
            ap.error("--url is required unless --snapshot is used")
        ev = {"pods": collect_pods(a.namespace, a.release), "warning_events": collect_events(a.namespace),
              "logs": collect_logs(a.namespace, a.release), "smoke": smoke(a.url, a.wait_seconds)}

    res = decide(ev)
    md = to_markdown(res, ev, {"cloud": a.cloud, "release": a.release})
    with open(f"{a.out}.json", "w") as f:
        json.dump({"result": res, "evidence": ev}, f, indent=2)
    with open(f"{a.out}.md", "w") as f:
        f.write(md)
    print(md)
    set_outputs(verdict=res["outcome"])
    return {"healthy": 0, "unhealthy": 1, "uncertain": 2}[res["outcome"]]


if __name__ == "__main__":
    sys.exit(main())
