# Capacity Investigator — Phase 1 (Investigation Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline-testable "investigation core" on top of the existing detectors/collectors — coverage-honest, abstaining, evidence-citing playbooks (`investigate_user`, `investigate_capacity_spike`) plus the supporting tools, an investigation reasoner, and an eval harness — so the agent *investigates and explains* instead of dumping a fixed audit.

**Architecture:** Deterministic detectors remain the source of truth ("is there a problem"); a new investigation layer assembles evidence from collectors + detectors, the reasoner only *explains/hypothesizes over confirmed evidence and abstains when evidence is thin*. Everything is pure/dependency-injected and runs offline with fake collectors — the Databricks ResponsesAgent/App wrapper is a later phase. No live Databricks needed for Phase 1.

**Tech Stack:** Python ≥3.10 stdlib only for the core (per repo convention); pytest; existing `fabric_audit_agent` package (functional core + dict-style ports). Optional `.[prod]`/`.[mcp]` extras unchanged.

## Spec sources
- `research/agent-arch/10-rerun-verdict.md` (the 5 must-fixes + the detectors-ground-the-LLM refinement + HolmesGPT patterns)
- `research/agent-arch/00-INDEX.md`, `14-per-user-cu-attribution-methods.md`, `18-mcp-protocol-anthropic-claude-api.md`
- `docs/superpowers/specs/2026-06-26-eventhouse-user-data-design.md`

## Global Constraints
- **Read-only is absolute.** No write/refresh/scale/delete. Tools are read-and-return; never persist from the interactive path.
- **Data dict keys stay camelCase** (`peakCuPct`, `sharePct`, `topUsers`, `cuSeconds`); Python identifiers snake_case.
- **Core depends on stdlib only.** No new third-party imports in the investigation core (the Claude path stays behind the existing optional client builders).
- **Every investigation output carries `coverage`, `confidence`, and `evidence`.** Never state a share as authoritative when `attributionMode == "cost"` (it's a proxy → label "monitored CU").
- **Abstain over guess:** when the requested entity isn't in the data or no live source is configured, return an explicit `abstained: true` result, never a fabricated finding.
- **Run the whole suite green after every task:** `cd fabric-audit-agent-py && python -m pytest -q` (baseline: 302 passed, 1 skipped).

## Phase roadmap (each later phase = its own plan)
- **Phase 1 (this plan):** offline investigation core — evidence/coverage/confidence, baselines, granular tools, the two playbooks, the investigation reasoner, MCP wiring for arg-taking tools, eval harness, finish the user-concentration label fix.
- **Phase 2:** wrap the core as an MLflow `ResponsesAgent` (raw Anthropic tool-loop), log→register UC→deploy on a Databricks App, OBO read-only, MLflow tracing. (Live env.)
- **Phase 3:** metric/semantic layer (CU%/throttle/concentration as vetted KQL) + `SKILL.md` runbooks for flagship incidents + value dictionaries (the accuracy levers).
- **Phase 4:** watchdog — scheduled SP Job calling the agent, Delta findings + dedup, Activator/Teams alerts, production monitoring + the eval flywheel.
- **Phase 5:** memory (Delta/Lakebase), anti-exfil hardening (spotlighting/egress + Teams-output sanitization), FUAM integration for Item→Owner, optional LangGraph upgrade.

---

### Task 1: Evidence, coverage, and confidence helpers

**Files:**
- Create: `fabric_audit_agent/investigation/__init__.py`
- Create: `fabric_audit_agent/investigation/evidence.py`
- Test: `tests/test_investigation_evidence.py`

**Interfaces:**
- Produces:
  - `build_coverage(facts: dict) -> dict` → `{"workspacesSeen": [str], "sources": [str], "sourcesFailed": [str], "mode": "live"|"mock"}`
  - `assess_confidence(facts: dict, *, found: bool, corroborating_sources: int) -> dict` → `{"level": "high"|"medium"|"low"|"insufficient", "basis": str}`
  - `evidence_item(kind: str, summary: str, data: dict) -> dict` → `{"kind", "summary", "data"}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_investigation_evidence.py
from fabric_audit_agent.investigation.evidence import build_coverage, assess_confidence, evidence_item


def test_build_coverage_lists_workspaces_and_failed_sources():
    facts = {
        "items": [{"workspace": "Sales", "name": "A"}, {"workspace": "Ops", "name": "B"}],
        "users": [{"user": "x@co"}],
        "sourcesFailed": ["LA unreachable"],
    }
    cov = build_coverage(facts)
    assert set(cov["workspacesSeen"]) == {"Sales", "Ops"}
    assert cov["sourcesFailed"] == ["LA unreachable"]
    assert cov["mode"] == "live"          # users/items present -> not the mock fallback


def test_assess_confidence_insufficient_when_not_found():
    c = assess_confidence({"users": []}, found=False, corroborating_sources=0)
    assert c["level"] == "insufficient"


def test_assess_confidence_high_when_corroborated():
    c = assess_confidence({"users": [{"user": "x"}]}, found=True, corroborating_sources=2)
    assert c["level"] == "high"


def test_evidence_item_shape():
    e = evidence_item("query", "top user by CpuTimeMs", {"user": "x", "cuSeconds": 10})
    assert e == {"kind": "query", "summary": "top user by CpuTimeMs", "data": {"user": "x", "cuSeconds": 10}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_evidence.py -q`
Expected: FAIL with `ModuleNotFoundError: fabric_audit_agent.investigation`

- [ ] **Step 3: Write minimal implementation**

```python
# fabric_audit_agent/investigation/__init__.py
"""Investigation layer: coverage-honest, abstaining, evidence-citing playbooks over the
deterministic detectors + collectors. Pure/offline — the Databricks agent wrapper is later."""
```

```python
# fabric_audit_agent/investigation/evidence.py
"""Evidence envelope + coverage + confidence helpers (pure).

Coverage honesty + abstention are must-fixes: every investigation states which workspaces/sources
it actually saw, and confidence is derived from evidence density + source corroboration (not the
model's say-so)."""


def build_coverage(facts):
    facts = facts or {}
    items = facts.get("items") or []
    workspaces = sorted({(it.get("workspace") or "") for it in items if it.get("workspace")})
    sources = []
    if facts.get("capacity"):
        sources.append("capacity")
    if facts.get("users") or items:
        sources.append("attribution")
    failed = list(facts.get("sourcesFailed") or [])
    # "mock" only when nothing real was collected; live sources always populate users/items/capacity.
    mode = "live" if (workspaces or facts.get("users") or facts.get("capacity")) else "mock"
    return {"workspacesSeen": workspaces, "sources": sources, "sourcesFailed": failed, "mode": mode}


def assess_confidence(facts, *, found, corroborating_sources):
    if not found:
        return {"level": "insufficient", "basis": "requested entity not present in collected data"}
    if corroborating_sources >= 2:
        return {"level": "high", "basis": f"{corroborating_sources} sources corroborate"}
    if corroborating_sources == 1:
        return {"level": "medium", "basis": "single source (CPU-time proxy, not authoritative CU)"}
    return {"level": "low", "basis": "weak/indirect evidence"}


def evidence_item(kind, summary, data):
    return {"kind": kind, "summary": summary, "data": data}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_evidence.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add fabric-audit-agent-py/fabric_audit_agent/investigation/ fabric-audit-agent-py/tests/test_investigation_evidence.py
git commit -m "feat(investigation): evidence envelope + coverage + confidence helpers"
```

---

### Task 2: Baseline computation (the CPU×duration model)

**Files:**
- Create: `fabric_audit_agent/investigation/baseline.py`
- Test: `tests/test_investigation_baseline.py`

**Interfaces:**
- Consumes: per-entity history rows shaped like the rollup output (`{"cuSeconds": float, "durationMs": float, "operation": str, "hourUtc": int}`); `durationMs`/`operation`/`hourUtc` optional.
- Produces:
  - `compute_baseline(rows: list[dict]) -> dict` → `{"count", "p50", "p95", "p99", "opMix": {str: int}, "peakHourUtc": int|None}`
  - `compare_to_baseline(today_cu: float, baseline: dict) -> dict` → `{"percentileRank": float, "deltaVsP50Pct": float|None, "shifted": bool}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_investigation_baseline.py
from fabric_audit_agent.investigation.baseline import compute_baseline, compare_to_baseline


def test_compute_baseline_percentiles_and_opmix():
    rows = [{"cuSeconds": c, "operation": "query", "hourUtc": 14} for c in (10, 20, 30, 40, 100)]
    b = compute_baseline(rows)
    assert b["count"] == 5
    assert b["p50"] == 30
    assert b["p95"] >= 40 and b["p99"] >= b["p95"]
    assert b["opMix"] == {"query": 5}
    assert b["peakHourUtc"] == 14


def test_compute_baseline_empty():
    b = compute_baseline([])
    assert b["count"] == 0 and b["p50"] is None and b["peakHourUtc"] is None


def test_compare_to_baseline_flags_outlier():
    b = compute_baseline([{"cuSeconds": c} for c in (10, 20, 30, 40, 50)])
    today = compare_to_baseline(500, b)
    assert today["percentileRank"] == 100.0 and today["shifted"] is True


def test_compare_to_baseline_normal_run_not_shifted():
    b = compute_baseline([{"cuSeconds": c} for c in (10, 20, 30, 40, 50)])
    assert compare_to_baseline(30, b)["shifted"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_baseline.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# fabric_audit_agent/investigation/baseline.py
"""Per-entity baselines: distribution (percentiles) + operation mix + peak hour, and a today-vs-baseline
comparison. Pure. Answers "is today abnormal vs this user's own history" — the CPU×duration model."""
import math


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = pct / 100.0 * (len(sorted_vals) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_vals[lo]
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def compute_baseline(rows):
    rows = rows or []
    cus = sorted(float(r.get("cuSeconds") or 0) for r in rows)
    op_mix = {}
    hours = {}
    for r in rows:
        op = r.get("operation")
        if op:
            op_mix[op] = op_mix.get(op, 0) + 1
        h = r.get("hourUtc")
        if h is not None:
            hours[h] = hours.get(h, 0) + 1
    peak_hour = max(hours, key=hours.get) if hours else None
    return {
        "count": len(cus),
        "p50": _percentile(cus, 50), "p95": _percentile(cus, 95), "p99": _percentile(cus, 99),
        "opMix": op_mix, "peakHourUtc": peak_hour,
    }


def compare_to_baseline(today_cu, baseline):
    cus_count = baseline.get("count") or 0
    p50 = baseline.get("p50")
    p95 = baseline.get("p95")
    today_cu = float(today_cu or 0)
    if not cus_count or p50 is None:
        return {"percentileRank": None, "deltaVsP50Pct": None, "shifted": False}
    # percentile rank of today vs the baseline cluster (rough: fraction at/below the p95 anchor)
    rank = 100.0 if (p95 is not None and today_cu >= p95) else (50.0 if today_cu >= p50 else 0.0)
    delta = ((today_cu - p50) / p50 * 100.0) if p50 else None
    shifted = bool(p95 is not None and today_cu > p95)
    return {"percentileRank": rank, "deltaVsP50Pct": delta, "shifted": shifted}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_baseline.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add fabric-audit-agent-py/fabric_audit_agent/investigation/baseline.py fabric-audit-agent-py/tests/test_investigation_baseline.py
git commit -m "feat(investigation): per-entity baselines + today-vs-baseline comparison"
```

---

### Task 3: Investigation reasoner (stub + Claude-backed), grounded + abstaining

**Files:**
- Create: `fabric_audit_agent/adapters/reasoner_investigation.py`
- Test: `tests/test_reasoner_investigation.py`

**Interfaces:**
- Consumes: an `evidence_bundle` dict `{"subject": str, "coverage": dict, "confidence": dict, "evidence": [evidence_item], "findings": [dict]}`.
- Produces:
  - `create_investigation_reasoner(client=None) -> {"investigate": fn(evidence_bundle) -> dict}` where the result is `{"explanation": str, "hypotheses": [str], "assumptions": [str], "confidence": str, "whatWouldConfirm": [str]}`.
  - The **stub** (client is None) is deterministic and never invents facts not in `evidence`; if `confidence.level == "insufficient"` it returns an explicit abstention explanation.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reasoner_investigation.py
from fabric_audit_agent.adapters.reasoner_investigation import create_investigation_reasoner


def _bundle(level="medium", findings=None):
    return {
        "subject": "user x@co",
        "coverage": {"workspacesSeen": ["Sales"], "sourcesFailed": []},
        "confidence": {"level": level, "basis": "single source"},
        "evidence": [{"kind": "query", "summary": "x@co = 40% monitored CU", "data": {"sharePct": 40}}],
        "findings": findings or [],
    }


def test_stub_abstains_when_insufficient():
    out = create_investigation_reasoner()["investigate"](_bundle(level="insufficient"))
    assert "insufficient" in out["explanation"].lower()
    assert out["confidence"] == "insufficient"
    assert out["hypotheses"] == []


def test_stub_grounds_in_evidence_and_states_assumptions():
    out = create_investigation_reasoner()["investigate"](_bundle())
    assert "40%" in out["explanation"]                 # cites the evidence figure
    assert any("monitored" in a.lower() or "proxy" in a.lower() for a in out["assumptions"])
    assert out["whatWouldConfirm"]                      # always offers a confirmation path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_reasoner_investigation.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# fabric_audit_agent/adapters/reasoner_investigation.py
"""Investigation reasoner — turns an assembled evidence bundle into an explanation + ranked
hypotheses + explicit assumptions + confidence + what-would-confirm.

Stub (no client): deterministic, grounded ONLY in the provided evidence, abstains when confidence is
insufficient. Claude path: same contract, sanitized + grounded prompt (wired in a later phase via
adapters.clients). Mirrors reasoner_claude's KB-fallback discipline: any failure -> stub output."""


def _stub_investigate(bundle):
    conf = (bundle.get("confidence") or {}).get("level", "low")
    cov = bundle.get("coverage") or {}
    ev = bundle.get("evidence") or []
    subject = bundle.get("subject", "the subject")

    if conf == "insufficient":
        seen = ", ".join(cov.get("workspacesSeen") or []) or "no workspaces"
        return {
            "explanation": (f"Analysis of {subject} is INSUFFICIENT: the evidence does not support a "
                            f"defensible conclusion (saw: {seen})."),
            "hypotheses": [], "assumptions": ["coverage is partial — enable monitoring on more workspaces"],
            "confidence": "insufficient",
            "whatWouldConfirm": ["enable Workspace Monitoring / Log Analytics on the relevant workspaces"],
        }

    cited = "; ".join(e.get("summary", "") for e in ev if e.get("summary"))
    return {
        "explanation": f"{subject}: {cited}." if cited else f"{subject}: see evidence.",
        "hypotheses": [e.get("summary") for e in ev if e.get("summary")][:3],
        "assumptions": ["CPU-time is a proxy for CU (monitored, not authoritative capacity CU)",
                        f"coverage limited to: {', '.join(cov.get('workspacesSeen') or []) or 'unknown'}"],
        "confidence": conf,
        "whatWouldConfirm": ["corroborate against Capacity Metrics / Capacity Events CU%"],
    }


def create_investigation_reasoner(client=None):
    if client is None:
        return {"investigate": _stub_investigate}

    def investigate(bundle):
        try:
            # The Claude path is wired in Phase 2; on any error fall back to the grounded stub.
            from .reasoner_claude import _first_text, _extract_json_array  # reuse existing helpers
            raise NotImplementedError  # placeholder until Phase 2 wiring; falls through to stub
        except Exception:
            return _stub_investigate(bundle)

    return {"investigate": investigate}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_reasoner_investigation.py -q`
Expected: PASS (3 passed). (The `NotImplementedError` placeholder is intentionally never reached by tests, which use the stub; it documents the Phase-2 seam and is exercised only via the fallback.)

- [ ] **Step 5: Commit**

```bash
git add fabric-audit-agent-py/fabric_audit_agent/adapters/reasoner_investigation.py fabric-audit-agent-py/tests/test_reasoner_investigation.py
git commit -m "feat(investigation): grounded, abstaining investigation reasoner (stub)"
```

---

### Task 4: Playbook — `investigate_user`

**Files:**
- Create: `fabric_audit_agent/investigation/playbooks.py`
- Test: `tests/test_investigation_playbooks.py`

**Interfaces:**
- Consumes: `collector` (dict-style port `{"collect": fn}` returning `facts`), `reasoner` (`{"investigate": fn}`), `evidence`/`baseline` helpers from Tasks 1–3.
- Produces:
  - `investigate_user(collector, reasoner, user, days=30, config=None) -> dict` →
    `{"subject", "abstained": bool, "coverage", "confidence", "evidence": [...], "result": {...reasoner output...}}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_investigation_playbooks.py
from fabric_audit_agent.investigation.playbooks import investigate_user
from fabric_audit_agent.adapters.reasoner_investigation import create_investigation_reasoner


def _facts(users):
    return {"capacity": {"peakCuPct": 120.0, "throttleMinutes": 10},
            "items": [{"workspace": "Sales", "name": "A4A", "sharePct": 90, "attributionMode": "cost",
                       "topUsers": [{"user": "x@co", "cuSeconds": 900}], "userCount": 1}],
            "users": users}


def _collector(facts):
    return {"collect": lambda: facts}


def test_investigate_user_found_builds_grounded_result():
    facts = _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90,
                     "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "x@co", days=30)
    assert out["abstained"] is False
    assert out["coverage"]["workspacesSeen"] == ["Sales"]
    assert any("A4A" in e["summary"] or "90" in str(e["data"]) for e in out["evidence"])
    assert "x@co" in out["result"]["explanation"]


def test_investigate_user_absent_abstains_not_hallucinates():
    facts = _facts([{"user": "someone@co", "cuSeconds": 5, "sharePct": 100, "topItems": [], "itemCount": 0}])
    out = investigate_user(_collector(facts), create_investigation_reasoner(), "ghost@co", days=30)
    assert out["abstained"] is True
    assert out["confidence"]["level"] == "insufficient"
    assert out["result"]["hypotheses"] == []     # never invents a cause for a user it can't see
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_playbooks.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# fabric_audit_agent/investigation/playbooks.py
"""Coded investigation playbooks (the high-stakes, reliable paths). Deterministic orchestration:
collect -> locate -> baseline/correlate -> assemble evidence -> reasoner explains/abstains.
Read-only; pure given injected collector + reasoner."""
from .evidence import build_coverage, assess_confidence, evidence_item


def investigate_user(collector, reasoner, user, days=30, config=None):
    facts = collector["collect"]() or {}
    coverage = build_coverage(facts)
    users = facts.get("users") or []
    match = next((u for u in users if (u.get("user") or "").lower() == (user or "").lower()), None)

    if match is None:
        confidence = assess_confidence(facts, found=False, corroborating_sources=0)
        bundle = {"subject": f"user {user}", "coverage": coverage, "confidence": confidence,
                  "evidence": [], "findings": []}
        return {"subject": f"user {user}", "abstained": True, "coverage": coverage,
                "confidence": confidence, "evidence": [], "result": reasoner["investigate"](bundle)}

    cap = facts.get("capacity") or {}
    corroborating = 1 + (1 if cap.get("peakCuPct") is not None else 0)
    confidence = assess_confidence(facts, found=True, corroborating_sources=corroborating)

    ev = [evidence_item("attribution",
                        f"{match['user']} = {round(match.get('sharePct', 0), 1)}% of monitored CU "
                        f"via {len(match.get('topItems') or [])} item(s)", match)]
    if cap.get("peakCuPct") is not None:
        ev.append(evidence_item("capacity",
                                f"capacity peaked {cap['peakCuPct']}% ({cap.get('throttleMinutes', 0)} min throttled)",
                                cap))

    bundle = {"subject": f"user {match['user']}", "coverage": coverage, "confidence": confidence,
              "evidence": ev, "findings": []}
    return {"subject": f"user {match['user']}", "abstained": False, "coverage": coverage,
            "confidence": confidence, "evidence": ev, "result": reasoner["investigate"](bundle)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_playbooks.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add fabric-audit-agent-py/fabric_audit_agent/investigation/playbooks.py fabric-audit-agent-py/tests/test_investigation_playbooks.py
git commit -m "feat(investigation): investigate_user playbook (grounded + abstaining)"
```

---

### Task 5: Playbook — `investigate_capacity_spike`

**Files:**
- Modify: `fabric_audit_agent/investigation/playbooks.py`
- Test: `tests/test_investigation_playbooks.py` (add cases)

**Interfaces:**
- Produces:
  - `investigate_capacity_spike(collector, reasoner, when=None, config=None) -> dict` (same envelope shape as `investigate_user`); abstains when `facts["capacity"]["peakCuPct"]` is absent (no live capacity signal).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_investigation_playbooks.py
from fabric_audit_agent.investigation.playbooks import investigate_capacity_spike


def test_capacity_spike_names_top_driver_when_throttled():
    facts = _facts([{"user": "x@co", "cuSeconds": 900, "sharePct": 90, "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}])
    out = investigate_capacity_spike(_collector(facts), create_investigation_reasoner())
    assert out["abstained"] is False
    assert any("120" in str(e["data"]) or "120" in e["summary"] for e in out["evidence"])  # peak CU%
    assert any("A4A" in e["summary"] for e in out["evidence"])                              # top item


def test_capacity_spike_abstains_without_capacity_signal():
    facts = {"items": [], "users": []}   # no capacity events wired
    out = investigate_capacity_spike(_collector(facts), create_investigation_reasoner())
    assert out["abstained"] is True and out["confidence"]["level"] == "insufficient"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_playbooks.py -q`
Expected: FAIL with `ImportError: cannot import name 'investigate_capacity_spike'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to fabric_audit_agent/investigation/playbooks.py

def investigate_capacity_spike(collector, reasoner, when=None, config=None):
    facts = collector["collect"]() or {}
    coverage = build_coverage(facts)
    cap = facts.get("capacity") or {}

    if cap.get("peakCuPct") is None:
        confidence = assess_confidence(facts, found=False, corroborating_sources=0)
        bundle = {"subject": "capacity spike", "coverage": coverage, "confidence": confidence,
                  "evidence": [], "findings": []}
        return {"subject": "capacity spike", "abstained": True, "coverage": coverage,
                "confidence": confidence, "evidence": [], "result": reasoner["investigate"](bundle)}

    items = sorted(facts.get("items") or [], key=lambda it: -(it.get("sharePct") or 0))
    top = items[0] if items else None
    corroborating = 1 + (1 if items else 0)
    confidence = assess_confidence(facts, found=True, corroborating_sources=corroborating)

    ev = [evidence_item("capacity",
                        f"capacity peaked {cap['peakCuPct']}% ({cap.get('throttleMinutes', 0)} min throttled)",
                        cap)]
    if top:
        label = "monitored CU" if top.get("attributionMode") == "cost" else "capacity CU"
        tu = (top.get("topUsers") or [{}])[0].get("user")
        ev.append(evidence_item("concentration",
                                f"\"{top.get('name')}\" = {round(top.get('sharePct', 0), 1)}% of {label}"
                                + (f" (top user {tu})" if tu else ""), top))

    bundle = {"subject": "capacity spike", "coverage": coverage, "confidence": confidence,
              "evidence": ev, "findings": []}
    return {"subject": "capacity spike", "abstained": False, "coverage": coverage,
            "confidence": confidence, "evidence": ev, "result": reasoner["investigate"](bundle)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_investigation_playbooks.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add fabric-audit-agent-py/fabric_audit_agent/investigation/playbooks.py fabric-audit-agent-py/tests/test_investigation_playbooks.py
git commit -m "feat(investigation): investigate_capacity_spike playbook"
```

---

### Task 6: Finish the user-concentration honesty fix (open must-fix)

**Files:**
- Modify: `fabric_audit_agent/detectors/user_concentration.py`
- Test: `tests/test_user_concentration.py` (add a case)

**Interfaces:**
- Behavior: when capacity CU% is present, the `what` text and `evidence` must label the number as an **estimate** and keep `monitoredSharePct` separate; the headline trip metric is the monitored share, with capacity context as secondary (per the review finding #1/#3). No signature change.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_user_concentration.py
from fabric_audit_agent.detectors.user_concentration import detect_user_concentration


def test_user_concentration_labels_estimate_and_keeps_monitored_share():
    facts = {"capacity": {"peakCuPct": 60.0},
             "users": [{"user": "x@co", "sharePct": 80, "cuSeconds": 800,
                        "topItems": [{"name": "A4A"}], "itemCount": 1}]}
    flags = detect_user_concentration(facts)
    f = next(f for f in flags if f["type"] == "capacity.user-concentration")
    assert f["evidence"]["estimated"] is True
    assert f["evidence"]["monitoredSharePct"] == 80          # raw monitored share preserved
    assert "est" in f["what"].lower()                         # the headline marks it an estimate
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_user_concentration.py::test_user_concentration_labels_estimate_and_keeps_monitored_share -q`
Expected: FAIL (the assertion on `"est" in what` and/or `monitoredSharePct`) — confirm the exact gap, then adjust the `what` string + evidence in `user_concentration.py` to satisfy it (keep `monitoredSharePct`, set `estimated`, and word the headline as an estimate). Read the current file first: `fabric_audit_agent/detectors/user_concentration.py`.

- [ ] **Step 3: Write minimal implementation**

Edit `detect_user_concentration` so the over-threshold `what` reads (when `cap_pct is not None`):
```python
"what": (f"{u['user']} is driving an estimated ~{_fmt(val)}% of capacity CU "
         f"(={_fmt(_share(u))}% of monitored CU × {_fmt(cap_pct)}% capacity util) — mostly via \"{top_item}\"."),
```
and ensure `evidence` already contains `"monitoredSharePct": round(_share(u), 1)` and `"estimated": cap_pct is not None` (both are present today — verify, keep). The `monitored CU` (no cap_pct) branch is unchanged.

- [ ] **Step 4: Run the full suite to verify nothing regresses**

Run: `cd fabric-audit-agent-py && python -m pytest -q`
Expected: PASS (all green, +1 new test)

- [ ] **Step 5: Commit**

```bash
git add fabric-audit-agent-py/fabric_audit_agent/detectors/user_concentration.py fabric-audit-agent-py/tests/test_user_concentration.py
git commit -m "fix(detector): label per-user capacity share as an estimate; keep monitored share separate"
```

---

### Task 7: MCP tools — `user_activity`, `investigate_user`, `investigate_capacity_spike` (with args)

**Files:**
- Modify: `fabric_audit_agent/tools.py`
- Modify: `fabric_audit_agent/mcp_server.py`
- Test: `tests/test_mcp_tools.py` (add cases)

**Interfaces:**
- Consumes: `_has_live_source`, `_build_collector` (existing in `tools.py`); the playbooks (Tasks 4–5); `create_investigation_reasoner` (Task 3); `create_stub_reasoner`/`create_mock_collector` (existing).
- Produces: `create_tool_definitions(base_dir=None)` returns, in addition to `run_audit` + `list_workspaces`, three tools:
  - `user_activity` — `input_schema {"user": {"type":"string"}}` (optional); no `user` → ranked top users; with `user` → that user's detail. Handler signature `handler(input=None)`.
  - `investigate_user` — `input_schema {"user": {"type":"string"}, "days": {"type":"integer"}}` (`user` required).
  - `investigate_capacity_spike` — `input_schema {"when": {"type":"string"}}` (optional).
- `build_mcp_server` registers tools **with their input parameters** (the current loop only handles no-arg tools — fix it to pass arguments through).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_mcp_tools.py
from fabric_audit_agent.tools import create_tool_definitions


def test_investigation_tools_defined_with_schemas():
    by_name = {d["name"]: d for d in create_tool_definitions()}
    assert {"run_audit", "list_workspaces", "user_activity", "investigate_user",
            "investigate_capacity_spike"} <= set(by_name)
    assert by_name["investigate_user"]["input_schema"]["properties"]["user"]["type"] == "string"


def test_investigate_user_handler_abstains_offline(monkeypatch):
    for v in ("FABRIC_CSV_PATHS", "FABRIC_CLIENT_ID", "FABRIC_KUSTO_CLUSTER",
              "FABRIC_CAPACITY_EVENTS_CLUSTER", "FABRIC_LA_WORKSPACE_ID"):
        monkeypatch.delenv(v, raising=False)
    h = next(d for d in create_tool_definitions() if d["name"] == "investigate_user")["handler"]
    out = h({"user": "anyone@co", "days": 30})   # no live source -> mock estate, user absent -> abstain
    assert out["abstained"] is True and "coverage" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_mcp_tools.py -q`
Expected: FAIL (`investigate_user` not in tool names)

- [ ] **Step 3: Write minimal implementation**

In `tools.py`, add a helper that yields a collector + reasoner (live or mock) reusing `_build_collector`, and append the three tool defs. Sketch (place inside `create_tool_definitions`, after the existing handlers):
```python
    from .adapters.reasoner_investigation import create_investigation_reasoner
    from .investigation.playbooks import investigate_user as _iu, investigate_capacity_spike as _ics

    def _collector_or_mock():
        col = _build_collector(os.environ)
        if col is None:
            col = create_mock_collector(os.path.join(base, "fixtures", "estate.json"))
        return col

    def user_activity_handler(_input=None):
        facts = _collector_or_mock()["collect"]()
        users = facts.get("users") or []
        who = (_input or {}).get("user")
        if who:
            u = next((x for x in users if (x.get("user") or "").lower() == who.lower()), None)
            return {"user": who, "found": u is not None, "detail": u,
                    "coverage": __import__("fabric_audit_agent.investigation.evidence", fromlist=["build_coverage"]).build_coverage(facts)}
        return {"topUsers": users[:10], "userCount": len(users),
                "coverage": __import__("fabric_audit_agent.investigation.evidence", fromlist=["build_coverage"]).build_coverage(facts)}

    def investigate_user_handler(_input=None):
        return _iu(_collector_or_mock(), create_investigation_reasoner(), (_input or {}).get("user"),
                   days=(_input or {}).get("days", 30))

    def investigate_spike_handler(_input=None):
        return _ics(_collector_or_mock(), create_investigation_reasoner(), (_input or {}).get("when"))
```
(Use a clean top-of-file `from .investigation.evidence import build_coverage` instead of the inline `__import__` if you prefer — the import-at-top is cleaner; the sketch shows intent.) Then add the three `{"name","description","input_schema","handler"}` dicts to the returned list with the schemas from **Interfaces**.

In `mcp_server.py`, replace the no-arg registration loop so each tool is registered with its parameters. Minimal version that supports the optional/required string+int args used here:
```python
    import functools
    for _def in create_tool_definitions(base_dir):
        props = (_def.get("input_schema") or {}).get("properties") or {}
        if not props:
            def _tool(handler=_def["handler"]):
                return handler()
            server.tool(name=_def["name"], description=_def["description"])(_tool)
        else:
            def _tool(handler=_def["handler"], user: str = None, days: int = 30, when: str = None):
                payload = {k: v for k, v in {"user": user, "days": days, "when": when}.items() if v is not None}
                return handler(payload)
            server.tool(name=_def["name"], description=_def["description"])(_tool)
```
(The shared `_tool(... user, days, when ...)` signature covers all three arg-taking tools; FastMCP exposes only the params named in each tool's schema via the description — acceptable for Phase 1. A per-tool typed signature is a Phase-2 refinement.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_mcp_tools.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite + commit**

Run: `cd fabric-audit-agent-py && python -m pytest -q` → all green.
```bash
git add fabric-audit-agent-py/fabric_audit_agent/tools.py fabric-audit-agent-py/fabric_audit_agent/mcp_server.py fabric-audit-agent-py/tests/test_mcp_tools.py
git commit -m "feat(mcp): expose user_activity + investigate_user/capacity_spike tools with args"
```

---

### Task 8: Investigation eval harness (golden cases + groundedness/coverage scorer + CLI)

**Files:**
- Create: `fabric_audit_agent/eval/__init__.py`
- Create: `fabric_audit_agent/eval/investigation_cases.json`
- Create: `fabric_audit_agent/eval/score_investigations.py`
- Modify: `fabric_audit_agent/__main__.py` (add `eval-investigations` subcommand)
- Test: `tests/test_eval_investigations.py`

**Interfaces:**
- Produces:
  - `score_case(case: dict) -> dict` → `{"name", "abstainOk": bool, "groundedOk": bool, "passed": bool}` where `groundedOk` = every hypothesis string is substring-traceable to some evidence summary/data, and `abstainOk` = abstains iff the case's `expectAbstain` is true.
  - `run_suite(path=None) -> dict` → `{"total", "passed", "cases": [...]}`.
  - CLI: `python -m fabric_audit_agent eval-investigations` prints a pass/total summary.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_investigations.py
from fabric_audit_agent.eval.score_investigations import run_suite


def test_suite_runs_and_all_golden_cases_pass():
    res = run_suite()
    assert res["total"] >= 2
    assert res["passed"] == res["total"]      # the shipped golden cases must pass on the stub
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_eval_investigations.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```json
// fabric_audit_agent/eval/investigation_cases.json
[
  {
    "name": "user-present-grounded",
    "playbook": "investigate_user",
    "args": {"user": "x@co", "days": 30},
    "facts": {"capacity": {"peakCuPct": 120.0, "throttleMinutes": 10},
              "items": [{"workspace": "Sales", "name": "A4A", "sharePct": 90, "attributionMode": "cost",
                         "topUsers": [{"user": "x@co", "cuSeconds": 900}], "userCount": 1}],
              "users": [{"user": "x@co", "cuSeconds": 900, "sharePct": 90,
                         "topItems": [{"name": "A4A", "cuSeconds": 900}], "itemCount": 1}]},
    "expectAbstain": false
  },
  {
    "name": "user-absent-abstains",
    "playbook": "investigate_user",
    "args": {"user": "ghost@co", "days": 30},
    "facts": {"items": [], "users": []},
    "expectAbstain": true
  }
]
```

```python
# fabric_audit_agent/eval/__init__.py
"""Offline eval harness for the investigation playbooks (golden cases + groundedness/coverage scorer)."""
```

```python
# fabric_audit_agent/eval/score_investigations.py
"""Score investigation playbooks against golden cases: groundedness (every hypothesis traces to
evidence) + coverage-honesty (abstain iff expected). Offline, uses the stub reasoner."""
import json
import os

from ..investigation.playbooks import investigate_user, investigate_capacity_spike
from ..adapters.reasoner_investigation import create_investigation_reasoner

_PLAYBOOKS = {"investigate_user": investigate_user, "investigate_capacity_spike": investigate_capacity_spike}
_CASES = os.path.join(os.path.dirname(__file__), "investigation_cases.json")


def _grounded(result):
    ev_text = " ".join(
        (e.get("summary", "") + " " + json.dumps(e.get("data", {}))) for e in result.get("evidence", [])
    ).lower()
    hyps = result.get("result", {}).get("hypotheses", []) or []
    if not hyps:
        return True   # nothing claimed -> nothing to ground
    # each hypothesis must share a meaningful token with the cited evidence
    return all(any(tok in ev_text for tok in h.lower().split() if len(tok) > 3) for h in hyps)


def score_case(case):
    pb = _PLAYBOOKS[case["playbook"]]
    collector = {"collect": lambda c=case: c["facts"]}
    args = case.get("args", {})
    if case["playbook"] == "investigate_user":
        result = pb(collector, create_investigation_reasoner(), args.get("user"), days=args.get("days", 30))
    else:
        result = pb(collector, create_investigation_reasoner(), args.get("when"))
    abstain_ok = bool(result["abstained"]) == bool(case.get("expectAbstain"))
    grounded_ok = _grounded(result)
    return {"name": case["name"], "abstainOk": abstain_ok, "groundedOk": grounded_ok,
            "passed": abstain_ok and grounded_ok}


def run_suite(path=None):
    with open(path or _CASES, "r", encoding="utf-8") as fh:
        cases = json.load(fh)
    results = [score_case(c) for c in cases]
    return {"total": len(results), "passed": sum(1 for r in results if r["passed"]), "cases": results}
```

In `__main__.py`, add a dispatch branch:
```python
    if cmd == "eval-investigations":
        from .eval.score_investigations import run_suite
        res = run_suite()
        print(f"Investigations: {res['passed']}/{res['total']} passed")
        for c in res["cases"]:
            print(f"  {'PASS' if c['passed'] else 'FAIL'} {c['name']} (abstain={c['abstainOk']} grounded={c['groundedOk']})")
        return
```
(Match the existing dispatch style in `__main__.py` — read it first to place the branch consistently with the other commands.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd fabric-audit-agent-py && python -m pytest tests/test_eval_investigations.py -q` → PASS.
Then smoke the CLI: `cd fabric-audit-agent-py && python -m fabric_audit_agent eval-investigations`
Expected: `Investigations: 2/2 passed`.

- [ ] **Step 5: Run the full suite + commit**

Run: `cd fabric-audit-agent-py && python -m pytest -q` → all green.
```bash
git add fabric-audit-agent-py/fabric_audit_agent/eval/ fabric-audit-agent-py/fabric_audit_agent/__main__.py fabric-audit-agent-py/tests/test_eval_investigations.py
git commit -m "feat(eval): investigation golden-case harness (groundedness + coverage-honesty)"
```

---

## Self-Review

**1. Spec coverage (vs `10-rerun-verdict.md` must-fixes + improvements):**
- Coverage honesty / BLIND-vs-EMPTY → Task 1 (`build_coverage`) + abstention in Tasks 4/5. ✓
- Abstention / "insufficient evidence" first-class → Tasks 3/4/5. ✓
- Evidence-cited claims + groundedness → Tasks 1/3 + the eval scorer Task 8. ✓
- Detectors-ground-the-LLM → playbooks assemble detector/collector facts; reasoner only explains (Tasks 4/5/3). ✓
- Per-user CU "estimate" honesty (review #1/#3) → Task 6. ✓
- `user_activity` tool (was task #40) + arg-taking MCP registration → Task 7. ✓
- Baselines / CPU×duration → Task 2 (wired deeper into playbooks in Phase 2). ✓
- *Deferred to later phases (correctly out of Phase-1 scope):* ResponsesAgent/App deploy + OBO (Phase 2); metric layer + runbooks + value dictionaries (Phase 3); watchdog + Activator/Teams + Delta dedup (Phase 4); anti-exfil spotlighting + memory + FUAM (Phase 5). Flagged in the roadmap, not silently dropped.

**2. Placeholder scan:** The only intentional "placeholder" is the `NotImplementedError` Phase-2 seam in Task 3's Claude path — it is documented, never hit by tests (stub is used), and exists to mark the wiring point. All other steps contain real code + exact commands.

**3. Type consistency:** `facts` keys (`capacity.peakCuPct`, `items[].sharePct/attributionMode/topUsers`, `users[].user/sharePct/topItems`) match the live `attribution_rollup`/`collector_capacity_events` outputs. The investigation envelope (`{subject, abstained, coverage, confidence, evidence, result}`) is identical across Tasks 4, 5, 7, 8. Reasoner output keys (`explanation, hypotheses, assumptions, confidence, whatWouldConfirm`) are consistent across Tasks 3 and 8.
