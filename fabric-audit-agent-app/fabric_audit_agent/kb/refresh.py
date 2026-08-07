REFRESH_PLAYBOOKS = {
    "refresh.credential": {
        "rootCause": "The refresh failed on an expired or invalid credential against the source.",
        "fixes": [
            "Re-enter/refresh the data source credentials in the semantic model settings.",
            "Check whether the service account's password/token is expiring or was rotated.",
            "Switch to a service principal or a credential that doesn't expire manually.",
        ],
        "owner": "Power BI team",
    },
    "refresh.gateway": {
        "rootCause": "The refresh failed because the on-premises data gateway was offline, "
                     "unreachable, or overloaded.",
        "fixes": [
            "Check the gateway service status and cluster health.",
            "Restart or fail over to a healthy gateway node.",
            "Add gateway capacity or spread load across a gateway cluster.",
        ],
        "owner": "Power BI team",
    },
    "refresh.timeout": {
        "rootCause": "The refresh failed because the source query timed out.",
        "fixes": [
            "Optimize the source query (indexes, filters) to run faster.",
            "Enable incremental refresh so each run processes less data.",
            "Increase the source timeout if the query is already as efficient as possible.",
        ],
        "owner": "Power BI team",
    },
    "refresh.concurrency": {
        "rootCause": "The refresh failed because a refresh concurrency/capacity limit was exceeded.",
        "fixes": [
            "Stagger refresh schedules so fewer datasets refresh at the same time.",
            "Move heavy datasets to a capacity with more refresh parallelism headroom.",
            "Reduce the number of concurrent refreshes configured for this capacity.",
        ],
        "owner": "Power BI team",
    },
    "refresh.constraint": {
        "rootCause": "The refresh failed on a data/constraint violation (e.g. a duplicate key).",
        "fixes": [
            "Check the source data for duplicate keys or values violating the model's constraints.",
            "Fix the upstream data quality issue or relax the model relationship's cardinality.",
            "Add a data-quality check upstream to catch this before it reaches the model.",
        ],
        "owner": "Report author + Power BI team",
    },
    "refresh.chronic": {
        "rootCause": "The same refresh error is repeating across multiple runs — a standing "
                     "problem, not a one-off transient blip.",
        "fixes": [
            "Investigate the recurring error code/text (see the per-failure sub-cause finding).",
            "Fix the underlying root cause rather than relying on refresh retries.",
            "Add alerting so a chronic failure is caught before it becomes stale data.",
        ],
        "owner": "Power BI team",
    },
    "refresh.retry-storm": {
        "rootCause": "A single refresh retried an excessive number of times, indicating an "
                     "unstable source or an underlying failure being masked by retries.",
        "fixes": [
            "Check the source/gateway health during the refresh window.",
            "Investigate why the refresh needed that many retries instead of failing fast.",
            "Cap retry counts and alert instead of silently retrying indefinitely.",
        ],
        "owner": "Power BI team",
    },
}
