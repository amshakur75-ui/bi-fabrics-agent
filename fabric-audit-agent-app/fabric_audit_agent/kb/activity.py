ACTIVITY_PLAYBOOKS = {
    "activity.user-baseline-deviation": {
        "rootCause": "One operation cost far more CPU-time than this user's OWN 14-day p95 — a "
                     "per-user anomaly, not a 'this user is heavy' judgement. Their normal is the "
                     "yardstick, so a light user doing something unusual shows up and a "
                     "consistently heavy user doing their usual work does not.",
        "fixes": [
            "Open the named operation and compare it to what that user normally runs — a new "
            "filter removed, a date range widened, or a first run against a much larger model.",
            "If it landed within minutes of a capacity event, treat it as a likely contributor "
            "to that event rather than an isolated issue.",
            "If the baseline came from the estate-wide fallback, the user simply has no history "
            "yet — confirm against their own trend before acting on the multiple.",
        ],
        "owner": "Report author + Power BI team",
    },
    "activity.slow-operation": {
        "rootCause": "A single operation ran far longer (or cost far more CU-s) than normal, "
                     "independent of any share-of-capacity — a heavy query or report action.",
        "fixes": [
            "Open the query/report and check for a missing filter or an unintentionally wide scan.",
            "Check whether the item's data volume or model design changed recently.",
            "If it's a scheduled/automated job, confirm it's still needed at this size.",
        ],
        "owner": "Report author + Power BI team",
    },
    "activity.recurring-shape": {
        "rootCause": "The same expensive query shape is recurring across many events — a "
                     "report/visual design issue, not a one-off slow query.",
        "fixes": [
            "Identify the visual/measure generating the recurring shape and simplify it.",
            "Reduce the number of visuals hitting the model with this shape, or cache results.",
            "Consider aggregations or a composite model to shrink the query cost.",
        ],
        "owner": "Report author",
    },
    "activity.long-running-cluster": {
        "rootCause": "The same item accumulated a cluster of independently long-running "
                     "operations — points at the item's design, not any one user.",
        "fixes": [
            "Profile the item's slowest visuals/measures and optimize the model.",
            "Check for DirectQuery or an oversized/expensive query pattern on this item.",
            "Split heavy pages or add aggregations to cut typical query duration.",
        ],
        "owner": "Report author + Power BI team",
    },
}
