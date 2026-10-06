"""Small shared helpers for the AI tools."""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


def load_schema(name: str) -> dict:
    return json.loads((HERE / "schemas" / f"{name}.json").read_text())


def load_prompt(name: str) -> str:
    return (HERE / "prompts" / f"{name}.md").read_text()


def set_outputs(**kv: str) -> None:
    """Expose values to later workflow steps ($GITHUB_OUTPUT). Values are pre-validated by callers."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as f:
        for k, v in kv.items():
            assert "\n" not in str(v), "refusing multi-line output"
            f.write(f"{k}={v}\n")
