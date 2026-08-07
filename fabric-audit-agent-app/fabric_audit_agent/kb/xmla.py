XMLA_PLAYBOOKS = {
    "xmla.auth": {
        "rootCause": "An authentication/token failure against the XMLA endpoint.",
        "fixes": [
            "Check the XMLA client's credentials/token expiry and refresh them.",
            "Confirm the caller still has the required workspace role for XMLA read/write.",
            "Verify the XMLA endpoint is enabled and reachable for this capacity/tenant setting.",
        ],
        "owner": "Power BI team",
    },
    "xmla.bad-request": {
        "rootCause": "A Bad Request was returned on a TMSL/XMLA command — a malformed or "
                     "unsupported request against the endpoint.",
        "fixes": [
            "Validate the TMSL/XMLA payload against the current schema before sending it.",
            "Check the client/tool version is compatible with the current XMLA endpoint.",
            "Reproduce with a minimal request to isolate which part of the command is invalid.",
        ],
        "owner": "Power BI team",
    },
    "xmla.timeout": {
        "rootCause": "The XML for Analysis request timed out.",
        "fixes": [
            "Check whether the underlying query/model operation is itself slow (see query findings).",
            "Increase the client-side XMLA timeout if the operation is expected to be long.",
            "Investigate capacity pressure at the time of the timeout.",
        ],
        "owner": "Power BI team",
    },
    "xmla.connection-drop": {
        "rootCause": "The XMLA connection was dropped or reset mid-operation.",
        "fixes": [
            "Check for network instability between the client and the XMLA endpoint.",
            "Retry with backoff; investigate if this correlates with capacity throttling or restarts.",
            "Confirm the endpoint wasn't mid-failover/rebalance (see the 'session moved' suppression).",
        ],
        "owner": "Power BI team",
    },
}
