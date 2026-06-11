from fabric_audit_agent.diagnosis import diagnose, format_diagnosis


def _throttling_facts():
    return {"capacity": {
        "tenant": "Acme", "capacityId": "PROD", "sku": "F64", "memoryGB": 64,
        "peakCuPct": 95, "peakAt": "2026-06-09T10:00", "throttleMinutes": 20,
        "refreshes": [
            {"workspace": "Fin", "dataset": "A", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 6},
            {"workspace": "Fin", "dataset": "B", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 1},
            {"workspace": "Fin", "dataset": "C", "scheduledAt": "06:00", "durationMin": 10, "sizeGB": 1},
        ],
    }}


def test_diagnose_optimize_with_findings():
    r = diagnose(_throttling_facts())
    assert len(r["findings"]) > 0
    assert r["verdict"]["decision"] == "optimize"
    assert 0 <= r["health"]["overall"] <= 100


def test_format_renders_findings_and_verdict():
    text = format_diagnosis(diagnose(_throttling_facts()))
    assert "DIAGNOSIS" in text and "Capacity verdict:" in text


def test_format_clean_estate():
    text = format_diagnosis({"findings": [], "health": {"overall": 100, "byDomain": {}},
                             "verdict": {"decision": "healthy", "reason": "ok"}, "roadmap": []})
    assert "No issues detected" in text
