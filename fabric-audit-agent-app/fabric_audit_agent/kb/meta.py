META_PLAYBOOKS = {
    "meta.attribution-unmeasurable": {
        "rootCause": (
            "Monitored activity came back for this window, but no row carried a usable cost "
            "column, so no per-item share could be computed. This is a COVERAGE GAP, not a "
            "healthy estate -- the concentration analysis produced nothing because it could not "
            "measure, not because there was nothing to find."),
        "fixes": [
            "Check the attribution query's cost alias -- the deployed Log Analytics query "
            "summarises `cpuMs=sum(CpuTimeMs)`; a rename or a schema change breaks the rollup.",
            "Confirm CpuTimeMs (or DurationMs as the fallback) is still present on the source "
            "table for the operations in this window.",
            "Until this clears, treat any 'no concentration detected' result for this window as "
            "unknown rather than negative.",
        ],
        "owner": "Power BI team / agent maintainer",
    },
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
