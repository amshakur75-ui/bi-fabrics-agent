from fabric_audit_agent.narrative import exec_narrative
from fabric_audit_agent.report_md import build_markdown_report
from fabric_audit_agent.audience import view_for
from fabric_audit_agent.teams_card import build_teams_card
from fabric_audit_agent.coaching import get_user_tip


# ---- narrative ----
def test_exec_narrative_full():
    v = {"health": 86, "critical": 1, "warning": 3, "verdict": "optimize", "accountability": 2, "topFindings": [{"what": "Fix GL", "level": "Critical"}]}
    s = exec_narrative(v)
    assert "Estate health is 86/100 with 1 critical and 3 warning" in s
    assert "optimization opportunities remain" in s
    assert "2 issue(s) have been flagged repeatedly" in s
    assert "Top priority: Fix GL" in s


def test_exec_narrative_defaults():
    s = exec_narrative({})
    assert "Estate health is —/100 with 0 critical and 0 warning" in s
    assert "status is unclear" in s


# ---- audience ----
def test_view_for_exec():
    env = {"data": {"healthScore": {"overall": 80}, "verdict": {"decision": "optimize"},
                    "findings": [{"score": {"level": "Critical"}}, {"score": {"level": "Warning"}}],
                    "roadmap": [{"what": "A", "level": "Critical"}, {"what": "B", "level": "Warning"}],
                    "accountability": {"ignoredCount": 2}}}
    v = view_for(env, "exec")
    assert v["health"] == 80 and v["verdict"] == "optimize"
    assert v["critical"] == 1 and v["warning"] == 1
    assert v["topFindings"][0] == {"what": "A", "level": "Critical"} and v["accountability"] == 2


def test_view_for_author_and_team():
    env = {"data": {"findings": [{"what": "GL bidi", "userTip": "fix it"}, {"what": "no tip"}], "roadmap": [{"rank": 1}]}}
    assert view_for(env, "author")["items"] == [{"what": "GL bidi", "tip": "fix it"}]
    t = view_for(env, "team")
    assert t["audience"] == "team" and len(t["findings"]) == 2 and t["roadmap"] == [{"rank": 1}]


def test_narrative_from_exec_view_integration():
    env = {"data": {"healthScore": {"overall": 90}, "verdict": {"decision": "size-up"},
                    "findings": [{"score": {"level": "Critical"}}], "roadmap": [{"what": "Buy", "level": "Critical"}]}}
    s = exec_narrative(view_for(env, "exec"))
    assert "90/100" in s and "a capacity increase is warranted" in s and "Top priority: Buy" in s


# ---- report-md ----
def test_build_markdown_report():
    env = {"summary": "Audit done", "data": {
        "tenant": "Acme",
        "healthScore": {"overall": 78, "byDomain": {"capacity": 83, "model": 91}},
        "verdict": {"decision": "optimize", "reason": "fixable first"},
        "roadmap": [{"rank": 1, "level": "Critical", "what": "GL", "fix": "incremental refresh"}],
        "findings": [{"score": {"level": "Critical"}, "what": "GL 70%", "where": "Fin", "why": "noisy", "impact": "slow", "fix": ["a", "b"]}],
        "correlations": [{"theme": "capacity-pressure", "narrative": "throttle driven by drivers"}],
    }}
    md = build_markdown_report(env)
    assert "# Fabric Audit Report" in md and "_Audit done_" in md and "Tenant: **Acme**" in md
    assert "## Health: 78/100" in md and "| capacity | 83 |" in md
    assert "## Capacity verdict: OPTIMIZE" in md and "fixable first" in md
    assert "1. **[Critical]** GL — _Fix:_ incremental refresh" in md
    assert "### [Critical] GL 70%" in md and "- **Fix:** a; b" in md
    assert "- **capacity-pressure:** throttle driven by drivers" in md


def test_build_markdown_report_minimal():
    md = build_markdown_report({})
    assert "# Fabric Audit Report" in md and "## Findings (0)" in md


# ---- teams-card ----
def test_build_teams_card():
    env = {"summary": "Audit", "data": {
        "verdict": {"decision": "optimize", "reason": "fix first"},
        "findings": [{"score": {"level": "Critical"}, "what": "GL 70%", "fix": ["incremental refresh"]}, {"score": {"level": "Warning"}, "what": "x"}],
    }}
    card = build_teams_card(env)
    assert card["type"] == "message" and card["summary"] == "Audit"
    headings = [s["heading"] for s in card["sections"]]
    assert "Summary" in headings and "Capacity verdict" in headings and "Critical findings (1)" in headings
    crit = next(s for s in card["sections"] if s["heading"].startswith("Critical"))
    assert crit["items"] == ["GL 70% — Fix: incremental refresh"]


def test_teams_card_default_summary_and_no_fix():
    assert build_teams_card({})["summary"] == "Fabric audit"
    assert build_teams_card(None)["summary"] == "Fabric audit"
    card = build_teams_card({"data": {"findings": [{"score": {"level": "Critical"}, "what": "no-fix"}]}})
    crit = next(s for s in card["sections"] if s["heading"].startswith("Critical"))
    assert crit["items"] == ["no-fix — Fix: see report"]


def test_teams_card_no_verdict_omits_section():
    card = build_teams_card({"summary": "s", "data": {"findings": []}})
    assert "Capacity verdict" not in [s["heading"] for s in card["sections"]]


def test_teams_card_partial_verdict_renders_like_js_string():
    # Node String(undefined).toUpperCase() -> "UNDEFINED"; missing reason -> "undefined" (never throws).
    card = build_teams_card({"data": {"verdict": {"reason": "r"}, "findings": []}})
    v = next(s for s in card["sections"] if s["heading"] == "Capacity verdict")
    assert v["text"] == "UNDEFINED — r"
    card2 = build_teams_card({"data": {"verdict": {"decision": "optimize"}, "findings": []}})
    v2 = next(s for s in card2["sections"] if s["heading"] == "Capacity verdict")
    assert v2["text"] == "OPTIMIZE — undefined"


def test_teams_card_missing_what_renders_undefined():
    card = build_teams_card({"data": {"findings": [{"score": {"level": "Critical"}, "fix": ["f"]}]}})
    crit = next(s for s in card["sections"] if s["heading"].startswith("Critical"))
    assert crit["items"] == ["undefined — Fix: f"]


def test_teams_card_caps_criticals_at_10():
    findings = [{"score": {"level": "Critical"}, "what": f"c{i}", "fix": ["x"]} for i in range(15)]
    card = build_teams_card({"data": {"findings": findings}})
    crit = next(s for s in card["sections"] if s["heading"].startswith("Critical"))
    assert crit["heading"] == "Critical findings (15)" and len(crit["items"]) == 10


# ---- coaching ----
def test_get_user_tip():
    assert "single-direction" in get_user_tip("model.bidirectional")
    assert get_user_tip("capacity.throttle") is None
