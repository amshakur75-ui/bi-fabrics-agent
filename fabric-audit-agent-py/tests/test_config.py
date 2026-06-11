from fabric_audit_agent.config import DEFAULT_CONFIG, merge_config


def test_defaults_present():
    assert DEFAULT_CONFIG["capacity"]["throttleWarnPct"] == 80
    assert DEFAULT_CONFIG["capacity"]["concentrationPct"] == 30
    assert DEFAULT_CONFIG["report"]["slowVisualMs"] == 5000


def test_merge_overrides_one_key_keeps_other_defaults():
    cfg = merge_config({"capacity": {"throttleWarnPct": 70}})
    assert cfg["capacity"]["throttleWarnPct"] == 70      # overridden
    assert cfg["capacity"]["contentionMin"] == 3         # sibling default kept
    assert cfg["model"]["bidirectionalMin"] == 4         # other domains intact


def test_merge_does_not_mutate_defaults():
    merge_config({"capacity": {"throttleWarnPct": 1}})
    assert DEFAULT_CONFIG["capacity"]["throttleWarnPct"] == 80


def test_merge_carries_through_unknown_domains():
    cfg = merge_config({"custom": {"x": 1}})
    assert cfg["custom"]["x"] == 1


def test_merge_no_args_returns_defaults_copy():
    cfg = merge_config()
    assert cfg["capacity"]["concentrationCritPct"] == 50
    cfg["capacity"]["concentrationCritPct"] = 999
    assert DEFAULT_CONFIG["capacity"]["concentrationCritPct"] == 50   # copy, not alias
