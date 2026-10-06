#!/usr/bin/env python3
"""AI-assisted deployment planning for ChatOps comments such as `/deploy-preview feature-x on gcp`.

Flow:  comment --LLM--> schema-validated plan --guardrails--> allow-listed helm command (rendered by CODE).
The LLM decides WHAT (cloud, profile, ttl). Code decides HOW (the exact command), so a hostile or
confused model can never inject shell, escalate privileges, or touch another namespace.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys

from common import load_prompt, load_schema, set_outputs
from llm import LLMError, generate_json

CLOUDS = ("aws", "gcp")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def fallback_plan(comment: str, branch: str, default_cloud: str) -> dict:
    """Deterministic parser used when the LLM is unavailable: pipeline never depends on AI uptime."""
    text = comment.lower()
    action = "unknown"
    if re.search(r"/(deploy|preview)", text):
        action = "deploy_preview"
    if re.search(r"/(teardown|destroy|cleanup)", text):
        action = "teardown_preview"
    cloud = "gcp" if re.search(r"\b(gcp|gke|google)\b", text) else "aws" if re.search(r"\b(aws|eks|amazon)\b", text) else default_cloud
    parts = comment.split()
    ref = parts[1] if len(parts) > 1 and REF_RE.match(parts[1]) else branch
    profile = "high-availability" if re.search(r"\b(ha|high.availability|production)\b", text) else "cost-sensitive"
    return {"action": action, "cloud": cloud, "ref": ref, "profile": profile, "ttl_hours": 8,
            "rationale": "Parsed deterministically (AI unavailable)."}


def sanitize(plan: dict, pr_number: int, pr_branch: str) -> dict:
    """Guardrails. Everything security-relevant is derived by code, never taken from the model."""
    warnings: list[str] = []
    safe = dict(plan)

    if safe["cloud"] not in CLOUDS:
        raise ValueError(f"cloud {safe['cloud']!r} not allowed")

    # Deployed code is ALWAYS the PR head; a different ref from the comment is ignored.
    if safe["ref"] != pr_branch:
        warnings.append(f"ref {safe['ref']!r} ignored; previews always deploy the PR head ({pr_branch!r})")
    safe["ref"] = pr_branch
    if not REF_RE.match(pr_branch) or ".." in pr_branch:
        raise ValueError("unsafe branch name")

    safe["ttl_hours"] = max(1, min(72, int(safe["ttl_hours"])))
    # Namespace/release are derived from the PR number only: one PR can never touch another's resources.
    safe["namespace"] = f"pr-{int(pr_number)}"
    safe["release"] = f"ib-pr-{int(pr_number)}"
    safe["warnings"] = warnings
    return safe


def render_commands(plan: dict, image_tag: str, registry: str) -> list[str]:
    """Exact commands the pipeline will run (logged for audit). Built only from validated fields."""
    ns, rel, cloud, profile = plan["namespace"], plan["release"], plan["cloud"], plan["profile"]
    chart = "charts/idea-board"
    if plan["action"] == "teardown_preview":
        return [shlex.join(["helm", "uninstall", rel, "-n", ns, "--ignore-not-found"]),
                shlex.join(["kubectl", "delete", "namespace", ns, "--ignore-not-found"])]
    return [
        shlex.join(["helm", "upgrade", "--install", rel, chart, "-n", ns, "--create-namespace",
                    "-f", f"{chart}/values/{cloud}.yaml", "-f", f"{chart}/values/profile-{profile}.yaml",
                    "--set", f"image.registry={registry}", "--set", f"image.tag={image_tag}",
                    "--set", "postgres.enabled=true", "--set", "postgres.persistence=false",
                    "--set", "autoscaling.enabled=false",
                    "--atomic", "--wait", "--timeout", "5m"]),
        shlex.join(["kubectl", "rollout", "status", f"deployment/{rel}-frontend", "-n", ns, "--timeout=120s"]),
    ]


def build_plan(comment: str, pr_number: int, pr_branch: str, default_cloud: str) -> tuple[dict, str]:
    prompt = (f"Default cloud: {default_cloud}\nPR branch: {pr_branch}\nPR number: {pr_number}\n"
              f"Comment (untrusted):\n<<<\n{comment[:500]}\n>>>")
    try:
        plan = generate_json(prompt, load_schema("deploy_plan"), system=load_prompt("deploy_plan"))
        return plan, "gemini"
    except LLMError as e:
        print(f"::warning::AI planner unavailable ({e}); using deterministic parser", file=sys.stderr)
        return fallback_plan(comment, pr_branch, default_cloud), "fallback"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comment", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--default-cloud", default="aws", choices=CLOUDS)
    ap.add_argument("--image-tag", default="latest")
    ap.add_argument("--registry", default="ghcr.io/OWNER")
    ap.add_argument("--out", default="deploy-plan.json")
    a = ap.parse_args()

    raw, source = build_plan(a.comment, a.pr, a.branch, a.default_cloud)
    try:
        plan = sanitize(raw, a.pr, a.branch)
    except ValueError as e:
        print(f"REJECTED by guardrails: {e}", file=sys.stderr)
        return 1
    plan["source"] = source
    cmds = render_commands(plan, a.image_tag, a.registry)

    with open(a.out, "w") as f:
        json.dump({**plan, "commands": cmds}, f, indent=2)
    print(json.dumps(plan, indent=2))
    print("\nCommands that will run:")
    for c in cmds:
        print(f"  $ {c}")
    set_outputs(action=plan["action"], cloud=plan["cloud"], profile=plan["profile"],
                namespace=plan["namespace"], release=plan["release"], ttl_hours=plan["ttl_hours"], source=source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
