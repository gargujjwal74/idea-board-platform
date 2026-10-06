#!/usr/bin/env python3
"""Summarise `terraform show -json` into a risk-rated PR comment.

Layering: deterministic analysis first (counts, destroys, risky types), then the LLM explains and rates.
The final risk is max(deterministic_floor, llm_risk): the model can raise risk, never lower it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from common import load_prompt, load_schema, set_outputs
from llm import LLMError, generate_json

# Resources where delete/replace means potential data loss or an outage.
CRITICAL_TYPES = {
    "aws_db_instance", "aws_eks_cluster", "aws_vpc", "aws_eks_node_group",
    "google_sql_database_instance", "google_container_cluster", "google_compute_network",
}
ORDER = {"low": 0, "medium": 1, "high": 2}


def digest(plan: dict) -> dict:
    changes = [c for c in plan.get("resource_changes", []) if c["change"]["actions"] != ["no-op"] and c["change"]["actions"] != ["read"]]
    by_action: Counter = Counter()
    flagged: list[str] = []
    types: Counter = Counter()
    for c in changes:
        acts = c["change"]["actions"]
        label = "replace" if set(acts) == {"create", "delete"} else acts[0]
        by_action[label] += 1
        types[c["type"]] += 1
        if label in ("delete", "replace") and c["type"] in CRITICAL_TYPES:
            flagged.append(f"{label} of {c['type']} ({c['address']})")
        after = c["change"].get("after") or {}
        if c["type"] == "aws_security_group" or c["type"] == "aws_security_group_rule":
            for rule in (after.get("ingress") or []):
                if "0.0.0.0/0" in (rule.get("cidr_blocks") or []):
                    flagged.append(f"world-open ingress in {c['address']}")
        if c["type"] == "google_compute_firewall" and "0.0.0.0/0" in (after.get("source_ranges") or []):
            flagged.append(f"world-open firewall {c['address']}")
    return {
        "total_changes": len(changes),
        "by_action": dict(by_action),
        "resource_types": dict(types.most_common(15)),
        "flagged": flagged[:15],
    }


def deterministic_floor(d: dict) -> str:
    if d["flagged"]:
        return "high"
    if d["by_action"].get("delete") or d["by_action"].get("replace"):
        return "medium"
    if d["by_action"].get("update"):
        return "medium" if d["total_changes"] > 5 else "low"
    return "low"


def review(d: dict) -> tuple[dict, str]:
    floor = deterministic_floor(d)
    try:
        r = generate_json("Terraform plan digest:\n" + json.dumps(d, indent=2),
                          load_schema("plan_review"), system=load_prompt("plan_review"))
        source = "gemini"
    except LLMError as e:
        print(f"::warning::AI reviewer unavailable ({e}); deterministic summary only", file=sys.stderr)
        r = {"risk": floor, "recommendation": {"low": "approve", "medium": "review", "high": "review"}[floor],
             "summary": f"{d['total_changes']} resource change(s): {d['by_action']}. (AI summary unavailable.)",
             "concerns": d["flagged"]}
        source = "fallback"
    if ORDER[floor] > ORDER[r["risk"]]:
        r["risk"] = floor  # model can never lower the deterministic risk
        if r["recommendation"] == "approve":
            r["recommendation"] = "review"
    if d["flagged"] and r["risk"] == "high" and any("delete" in f or "replace" in f for f in d["flagged"]):
        r["recommendation"] = "block"
    return r, source


def to_markdown(r: dict, d: dict, source: str, cloud: str) -> str:
    icon = {"low": "🟢", "medium": "🟡", "high": "🔴"}[r["risk"]]
    lines = [f"### {icon} Terraform plan review ({cloud}) - risk **{r['risk']}**, recommendation **{r['recommendation']}**",
             "", r["summary"], ""]
    if r["concerns"]:
        lines += ["**Concerns**"] + [f"- {c}" for c in r["concerns"]] + [""]
    lines += [f"<sub>{d['total_changes']} change(s) {d['by_action']} - analysed by {source}; "
              f"deterministic checks set a risk floor the model cannot lower.</sub>"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-json", required=True, help="output of `terraform show -json tfplan`")
    ap.add_argument("--cloud", default="unknown")
    ap.add_argument("--out", default="plan-review.md")
    a = ap.parse_args()

    with open(a.plan_json) as f:
        d = digest(json.load(f))
    r, source = review(d)
    md = to_markdown(r, d, source, a.cloud)
    with open(a.out, "w") as f:
        f.write(md)
    print(md)
    set_outputs(risk=r["risk"], recommendation=r["recommendation"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
