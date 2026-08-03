REPORT_PLAYBOOKS = {
    "report.too-many-visuals": {
        "rootCause": "Each visual is a separate query; too many on one page floods the capacity.",
        "fixes": [
            "Reduce visuals per page (aim < 20).",
            "Split dense pages into focused pages.",
            "Use bookmarks/drill-through instead of showing everything at once.",
        ],
        "owner": "Report author",
    },
    "report.directquery": {
        "rootCause": "DirectQuery sends a live query per interaction, adding latency and source load.",
        "fixes": [
            "Switch to Import mode where data volume allows.",
            "Add aggregations for common queries.",
            "If DirectQuery is required, tune the source and limit visuals per page.",
        ],
        "owner": "Report author + Power BI team",
    },
    "report.slow-visual": {
        "rootCause": "A visual is slow — usually a heavy DAX measure or an expensive DirectQuery.",
        "fixes": [
            "Profile the visual with Performance Analyzer.",
            "Optimize the DAX measure (avoid row-by-row, use variables).",
            "Reduce the visual's granularity or add an aggregation.",
        ],
        "owner": "Report author",
    },
}
