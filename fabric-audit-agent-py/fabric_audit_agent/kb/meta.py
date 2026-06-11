META_PLAYBOOKS = {
    "meta.detector-error": {
        "rootCause": "A detector threw an error and was skipped, so some findings may be missing from this audit.",
        "fixes": [
            "Check the agent logs for the failing detector and input.",
            "Validate the collected facts shape (see the dataQuality report).",
            "Re-run once the underlying data issue is resolved.",
        ],
        "owner": "Power BI team / agent maintainer",
    },
}
