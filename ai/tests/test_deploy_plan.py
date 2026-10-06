import pytest

import deploy_plan as dp


def raw(**kw):
    base = {"action": "deploy_preview", "cloud": "aws", "ref": "feature-x", "profile": "cost-sensitive",
            "ttl_hours": 8, "rationale": "x"}
    base.update(kw)
    return base


def test_namespace_and_release_derived_from_pr_number_only():
    p = dp.sanitize(raw(), 42, "feature-x")
    assert p["namespace"] == "pr-42" and p["release"] == "ib-pr-42"


def test_ref_from_model_is_ignored_in_favour_of_pr_head():
    p = dp.sanitize(raw(ref="main"), 1, "feature-x")
    assert p["ref"] == "feature-x" and p["warnings"]


def test_ttl_clamped():
    assert dp.sanitize(raw(ttl_hours=9999), 1, "b")["ttl_hours"] == 72
    assert dp.sanitize(raw(ttl_hours=0), 1, "b")["ttl_hours"] == 1


def test_unknown_cloud_rejected():
    with pytest.raises(ValueError):
        dp.sanitize(raw(cloud="azure"), 1, "b")


@pytest.mark.parametrize("branch", ["x; rm -rf /", "a b", "$(curl evil)", "../../etc", "-rf", "a..b"])
def test_hostile_branch_rejected(branch):
    with pytest.raises(ValueError):
        dp.sanitize(raw(ref=branch), 1, branch)


def test_commands_are_rendered_from_validated_fields_only():
    p = dp.sanitize(raw(cloud="gcp", profile="high-availability"), 7, "feature-x")
    cmds = dp.render_commands(p, "sha-abc", "ghcr.io/me")
    assert "values/gcp.yaml" in cmds[0] and "profile-high-availability.yaml" in cmds[0]
    assert "-n pr-7" in cmds[0] and "image.tag=sha-abc" in cmds[0] and "--atomic" in cmds[0]
    assert all(";" not in c and "|" not in c for c in cmds)


def test_teardown_only_touches_its_own_namespace():
    p = dp.sanitize(raw(action="teardown_preview"), 7, "b")
    cmds = dp.render_commands(p, "t", "r")
    assert cmds == ["helm uninstall ib-pr-7 -n pr-7 --ignore-not-found", "kubectl delete namespace pr-7 --ignore-not-found"]


def test_fallback_parser():
    p = dp.fallback_plan("/deploy-preview my-branch on gcp", "feature-x", "aws")
    assert p["cloud"] == "gcp" and p["ref"] == "my-branch" and p["action"] == "deploy_preview"
    assert dp.fallback_plan("/teardown", "b", "aws")["action"] == "teardown_preview"
