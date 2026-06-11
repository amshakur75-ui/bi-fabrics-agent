COST_PLAYBOOKS = {
    "cost.unused-report": {
        "rootCause": "The report has had no views in 30 days.",
        "fixes": [
            "Confirm with the owner that it is no longer needed.",
            "Archive or delete it to cut clutter and refresh load.",
        ],
        "owner": "Power BI team",
    },
    "cost.idle-capacity": {
        "rootCause": "Premium capacity is provisioned but largely idle.",
        "fixes": [
            "Consolidate workloads onto fewer capacities.",
            "Downsize or pause the capacity outside business hours.",
            "Reassign workspaces to match real demand.",
        ],
        "owner": "Power BI / FinOps",
    },
}
