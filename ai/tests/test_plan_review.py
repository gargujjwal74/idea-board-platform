import plan_review as pr


def plan(*changes):
    return {"resource_changes": [{"address": a, "type": t, "change": {"actions": acts, "after": after or {}}} for a, t, acts, after in changes]}


def test_digest_flags_database_replace():
    d = pr.digest(plan(("module.db.aws_db_instance.this", "aws_db_instance", ["delete", "create"], None)))
    assert d["by_action"] == {"replace": 1} and "replace of aws_db_instance" in d["flagged"][0]
    assert pr.deterministic_floor(d) == "high"


def test_world_open_sg_flagged():
    d = pr.digest(plan(("sg", "aws_security_group", ["create"], {"ingress": [{"cidr_blocks": ["0.0.0.0/0"]}]})))
    assert pr.deterministic_floor(d) == "high"


def test_noop_ignored_and_pure_create_is_low():
    d = pr.digest(plan(("a", "aws_s3_bucket", ["no-op"], None), ("b", "aws_s3_bucket", ["create"], None)))
    assert d["total_changes"] == 1 and pr.deterministic_floor(d) == "low"


def test_llm_cannot_lower_risk(monkeypatch):
    d = pr.digest(plan(("db", "aws_db_instance", ["delete"], None)))
    monkeypatch.setattr(pr, "generate_json", lambda *a, **k: {"risk": "low", "summary": "fine", "concerns": [], "recommendation": "approve"})
    r, _ = pr.review(d)
    assert r["risk"] == "high" and r["recommendation"] == "block"


def test_fallback_when_llm_down(monkeypatch):
    def boom(*a, **k):
        raise pr.LLMError("down")
    monkeypatch.setattr(pr, "generate_json", boom)
    r, src = pr.review(pr.digest(plan(("b", "aws_s3_bucket", ["create"], None))))
    assert src == "fallback" and r["risk"] == "low"
