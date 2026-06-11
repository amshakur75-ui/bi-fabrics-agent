SECURITY_PLAYBOOKS = {
    "security.admin-grant": {
        "rootCause": "An admin-level role was granted on a sensitive workspace.",
        "fixes": [
            "Confirm the grant was authorized by the workspace owner.",
            "Apply least-privilege; prefer security groups over individual admins.",
            "Enable periodic access reviews.",
        ],
        "owner": "Power BI admin / security",
    },
    "security.external-share": {
        "rootCause": "Content was shared outside the organization.",
        "fixes": [
            "Confirm the external share is intended and compliant.",
            "Restrict external sharing in tenant settings if not needed.",
            "Apply sensitivity labels to governed content.",
        ],
        "owner": "Power BI admin / security",
    },
    "security.unusual-access": {
        "rootCause": "Access volume far exceeds the user's normal baseline.",
        "fixes": [
            "Confirm the activity is legitimate with the user.",
            "Check for credential compromise or a runaway script.",
            "Review the full audit trail for the account.",
        ],
        "owner": "Power BI admin / security",
    },
}
