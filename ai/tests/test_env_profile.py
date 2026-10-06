import env_profile as ep


def prof(**kw):
    p = dict(ep.STATIC["cost-sensitive"])
    p["autoscaling"] = dict(p["autoscaling"])
    p.update(kw)
    return p


def test_budget_cap():
    p, notes = ep.enforce(prof(node_count=6), max_nodes=4)
    assert p["node_count"] == 4 and notes


def test_ha_invariants_enforced_even_if_model_forgets():
    p = prof(high_availability=True, node_count=1, backend_replicas=1)
    p["autoscaling"].update(min_replicas=1, max_replicas=1)
    p, notes = ep.enforce(p, max_nodes=4)
    assert p["node_count"] >= 2 and p["backend_replicas"] >= 2 and p["autoscaling"]["min_replicas"] >= 2


def test_min_max_repaired():
    p = prof()
    p["autoscaling"].update(min_replicas=5, max_replicas=2)
    p, _ = ep.enforce(p, 4)
    assert p["autoscaling"]["max_replicas"] == 5


def test_fallback_keywords():
    assert ep.fallback("HA production")["high_availability"] is True
    assert ep.fallback("cheap staging")["high_availability"] is False


def test_file_mapping_matches_terraform_and_helm_keys():
    tf, helm = ep.to_files(ep.STATIC["high-availability"], "")
    assert set(tf) == {"node_count", "node_size", "db_size", "high_availability"}
    assert helm["autoscaling"]["minReplicas"] == 3
