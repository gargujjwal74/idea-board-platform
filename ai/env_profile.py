#!/usr/bin/env python3
"""Turn a high-level goal ("cost-sensitive staging", "HA production for 10k users") into validated
Terraform variables and Helm values.

Outputs (both are plain data files that later pipeline steps consume):
  <out>/profile.auto.tfvars.json   -> auto-loaded by Terraform
  <out>/profile.values.json        -> passed to `helm -f` (JSON is valid YAML)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from common import load_prompt, load_schema, set_outputs
from llm import LLMError, generate_json

STATIC = {
    "cost-sensitive": {
        "profile_name": "cost-sensitive", "node_count": 2, "node_size": "small", "db_size": "small",
        "high_availability": False, "backend_replicas": 1,
        "autoscaling": {"enabled": True, "min_replicas": 1, "max_replicas": 2, "cpu_target_percent": 80},
        "rationale": "Static cost-sensitive profile.",
    },
    "high-availability": {
        "profile_name": "high-availability", "node_count": 3, "node_size": "medium", "db_size": "medium",
        "high_availability": True, "backend_replicas": 3,
        "autoscaling": {"enabled": True, "min_replicas": 3, "max_replicas": 10, "cpu_target_percent": 60},
        "rationale": "Static high-availability profile.",
    },
}


def fallback(goal: str) -> dict:
    ha = re.search(r"\b(ha|high.availability|prod(uction)?|critical|resilien\w*)\b", goal.lower())
    return dict(STATIC["high-availability" if ha else "cost-sensitive"])


# Capacity floor per node size. Kubernetes system pods (kube-proxy, DNS, metrics, logging agents...)
# consume most of a small node: on GKE e2-medium ~911m of ~940m allocatable CPU is already requested,
# so a lone small node cannot schedule even one app pod. Learned from a real failed deploy.
MIN_NODES = {"small": 2, "medium": 1, "large": 1}


def enforce(p: dict, max_nodes: int) -> tuple[dict, list[str]]:
    """Cross-field guardrails the JSON Schema cannot express. Always applied, even to static profiles."""
    notes: list[str] = []
    a = p["autoscaling"]
    floor = MIN_NODES[p["node_size"]]
    if p["node_count"] < floor:
        notes.append(f"{p['node_size']} nodes need at least {floor} (system pods fill a single small node); raised from {p['node_count']}")
        p["node_count"] = floor
    if p["node_count"] > max_nodes:
        notes.append(f"node_count {p['node_count']} capped to budget limit {max_nodes}")
        p["node_count"] = max_nodes
    if a["min_replicas"] > a["max_replicas"]:
        notes.append("autoscaling min > max; max raised to min")
        a["max_replicas"] = a["min_replicas"]
    if p["high_availability"]:
        if p["node_count"] < 2:
            notes.append("HA requires >= 2 nodes; raised")
            p["node_count"] = min(2, max_nodes)
        if a["min_replicas"] < 2 or p["backend_replicas"] < 2:
            notes.append("HA requires >= 2 replicas; raised")
            a["min_replicas"] = max(2, a["min_replicas"])
            a["max_replicas"] = max(a["max_replicas"], a["min_replicas"])
            p["backend_replicas"] = max(2, p["backend_replicas"])
    return p, notes


def to_files(p: dict, region: str) -> tuple[dict, dict]:
    a = p["autoscaling"]
    tfvars = {"node_count": p["node_count"], "node_size": p["node_size"], "db_size": p["db_size"],
              "high_availability": p["high_availability"]}
    values = {
        "backend": {"replicas": p["backend_replicas"]},
        "frontend": {"replicas": max(1, min(p["backend_replicas"], 3))},
        "autoscaling": {"enabled": a["enabled"], "minReplicas": a["min_replicas"],
                        "maxReplicas": a["max_replicas"], "targetCPUUtilizationPercentage": a["cpu_target_percent"]},
    }
    return tfvars, values


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True, help='e.g. "cost-sensitive staging for a small team"')
    ap.add_argument("--out", default="generated")
    ap.add_argument("--max-nodes", type=int, default=int(os.environ.get("AI_MAX_NODES", "4")))
    a = ap.parse_args()

    try:
        profile = generate_json(f"Goal (untrusted):\n<<<\n{a.goal[:300]}\n>>>",
                                load_schema("env_profile"), system=load_prompt("env_profile"))
        source = "gemini"
    except LLMError as e:
        print(f"::warning::AI profiler unavailable ({e}); using static profile", file=sys.stderr)
        profile, source = fallback(a.goal), "fallback"

    profile, notes = enforce(profile, a.max_nodes)
    tfvars, values = to_files(profile, "")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "profile.auto.tfvars.json").write_text(json.dumps(tfvars, indent=2))
    (out / "profile.values.json").write_text(json.dumps(values, indent=2))

    print(f"Profile '{profile['profile_name']}' (source: {source})")
    print(json.dumps({"terraform": tfvars, "helm": values}, indent=2))
    print(f"Rationale: {profile['rationale']}")
    for n in notes:
        print(f"Guardrail: {n}")
    set_outputs(profile_name=re.sub(r"[^a-z0-9-]", "-", profile["profile_name"].lower())[:40], source=source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
