CAPACITY_PLAYBOOKS = {
    "capacity.throttle": {
        "rootCause": "CU demand exceeds the capacity SKU during peak windows, forcing throttling.",
        "fixes": [
            "Identify the top CU-consuming items during the peak window.",
            "Stagger heavy refreshes out of the peak window.",
            "If demand is structural after optimization, size up the capacity SKU.",
        ],
        "owner": "Power BI team",
    },
    "capacity.contention": {
        "rootCause": "Multiple large models refresh at the same time, queuing on one capacity.",
        "fixes": [
            "Stagger refresh start times across the hour.",
            "Move non-critical refreshes off the peak window.",
            "Enable incremental refresh to shrink each refresh job.",
        ],
        "owner": "Power BI team",
    },
    "capacity.oversized-model": {
        "rootCause": "Semantic model footprint is large relative to capacity memory.",
        "fixes": [
            "Enable incremental refresh.",
            "Add aggregations for high-grain tables.",
            "Remove unused columns and disable auto date/time.",
            "Reduce high-cardinality columns.",
        ],
        "owner": "Report author + Power BI team",
    },
    "capacity.concentration": {
        "rootCause": "A single item is consuming a large share of the capacity's CU — a 'noisy neighbor' that can starve other workloads on the same capacity.",
        "fixes": [
            "Identify the user(s) and workload driving it — interactive queries vs a scheduled refresh.",
            "If interactive: review the report/model with the user (fewer visuals, avoid DirectQuery, add aggregations).",
            "If a refresh: enable incremental refresh, stagger it out of the peak window, or isolate the item on its own capacity.",
            "Contact the item owner to confirm the usage is expected.",
        ],
        "owner": "Power BI team + item owner",
    },
    # Informational (the healthy, distributed-load case): no single user over the threshold. Given a
    # real KB entry so it never falls back to the "not yet in the knowledge base" developer placeholder.
    "capacity.user-ranking": {
        "rootCause": "Capacity CU is spread across many users rather than concentrated in one account — the healthy, distributed-load pattern. Listed so the \"who is driving CU\" question is still answered even when no single user stands out.",
        "fixes": [
            "No action needed — this is the distributed-load case, not a problem.",
            "Keep an eye on the top consumer over time; only investigate if one user's share climbs toward the concentration threshold.",
        ],
        "owner": "Power BI team",
    },
}
