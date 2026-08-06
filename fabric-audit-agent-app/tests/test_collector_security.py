"""FIX C: security/access collector (activity events) -> facts["access"] -> detect_security."""
from fabric_audit_agent.adapters.collector_security import create_security_collector
from fabric_audit_agent.adapters.collector_merge import merge_facts_list
from fabric_audit_agent.detectors import detect_all


class _Http:
    """Serves activity-event pages in order; a page without continuationUri ends paging."""
    def __init__(self, pages):
        self._pages = list(pages)
        self._i = 0

    def get_json(self, url):
        page = self._pages[self._i] if self._i < len(self._pages) else {}
        self._i += 1
        return page


def test_external_share_only_flags_outside_domains():
    events = [{"Operation": "ShareReport", "WorkspaceName": "Finance", "ReportName": "Q4 Deck",
               "UserId": "alice@newellco.com", "CreationTime": "2026-08-06T10:00:00Z",
               "SharingInformation": [{"RecipientEmail": "bob@newellco.com"},
                                      {"RecipientEmail": "ext@vendor.com"}]}]
    col = create_security_collector(_Http([{"activityEventEntities": events}]), {})
    access = col["collect"]()["access"]
    shares = access["externalShares"]
    assert len(shares) == 1 and shares[0]["sharedWith"] == "ext@vendor.com"   # internal recipient excluded
    assert shares[0]["workspace"] == "Finance" and shares[0]["item"] == "Q4 Deck"
    assert "security.external-share" in {f["type"] for f in detect_all({"access": access})}


def test_orgdomains_allowlist_suppresses_partner():
    events = [{"Operation": "ShareReport", "UserId": "a@newellco.com", "WorkspaceName": "W",
               "ReportName": "R", "SharingInformation": [{"RecipientEmail": "x@partner.com"}]}]
    col = create_security_collector(_Http([{"activityEventEntities": events}]),
                                    {"orgDomains": ["newellco.com", "partner.com"]})
    assert col["collect"]()["access"]["externalShares"] == []   # partner.com is allowlisted


def test_admin_grant_sensitive_only_for_configured_workspace():
    events = [{"Operation": "AddWorkspaceUsersAdminAPI", "WorkspaceName": "Secret WS",
               "UserId": "admin@x.com", "CreationTime": "t", "OrgAppPermission": "Admin",
               "TargetUserOrGroupName": "mallory@x.com"}]
    col = create_security_collector(_Http([{"activityEventEntities": events}]),
                                    {"sensitiveWorkspaces": ["Secret WS"]})
    access = col["collect"]()["access"]
    grants = access["adminGrants"]
    assert len(grants) == 1 and grants[0]["sensitive"] is True and grants[0]["role"] == "Admin"
    assert "security.admin-grant" in {f["type"] for f in detect_all({"access": access})}


def test_admin_grant_not_sensitive_by_default_never_flags():
    events = [{"Operation": "AddWorkspaceUsersAdminAPI", "WorkspaceName": "Normal WS",
               "OrgAppPermission": "Admin", "TargetUserOrGroupName": "u@x.com", "UserId": "a@x.com"}]
    col = create_security_collector(_Http([{"activityEventEntities": events}]), {})  # no sensitive list
    access = col["collect"]()["access"]
    assert access["adminGrants"][0]["sensitive"] is False
    assert "security.admin-grant" not in {f["type"] for f in detect_all({"access": access})}


def test_security_fail_open():
    class _Boom:
        def get_json(self, url):
            raise RuntimeError("activity events unavailable")
    access = create_security_collector(_Boom(), {})["collect"]()["access"]
    assert access == {"adminGrants": [], "externalShares": [], "accessEvents": []}


def test_merge_preserves_access_dict():
    merged = merge_facts_list([
        {"capacity": {"peakCuPct": 50}},
        {"access": {"externalShares": [{"item": "R", "sharedWith": "x@ext.com"}],
                    "adminGrants": [], "accessEvents": []}},
    ])
    assert merged["access"]["externalShares"] and "adminGrants" in merged["access"]
