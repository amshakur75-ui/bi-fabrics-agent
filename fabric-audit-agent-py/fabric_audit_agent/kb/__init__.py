"""Remediation knowledge base. Faithful port of the Node ``core/kb/``.

Aggregates per-domain playbooks (anti-pattern -> rootCause -> fixes -> owner) and
resolves a flag type to its remediation, with a safe default for unknown types.
"""
from .capacity import CAPACITY_PLAYBOOKS
from .model import MODEL_PLAYBOOKS
from .report import REPORT_PLAYBOOKS
from .pipeline import PIPELINE_PLAYBOOKS
from .lineage import LINEAGE_PLAYBOOKS
from .security import SECURITY_PLAYBOOKS
from .cost import COST_PLAYBOOKS
from .meta import META_PLAYBOOKS

_ALL = {
    **CAPACITY_PLAYBOOKS, **MODEL_PLAYBOOKS, **REPORT_PLAYBOOKS, **PIPELINE_PLAYBOOKS,
    **LINEAGE_PLAYBOOKS, **SECURITY_PLAYBOOKS, **COST_PLAYBOOKS, **META_PLAYBOOKS,
}

_DEFAULT = {
    "rootCause": "Pattern not yet in the knowledge base.",
    "fixes": ["Investigate manually and add a playbook entry."],
    "owner": "Power BI team",
}


def get_remediation(flag_type):
    """Return {rootCause, fixes, owner} for a flag type, or a safe default."""
    return _ALL.get(flag_type, _DEFAULT)
