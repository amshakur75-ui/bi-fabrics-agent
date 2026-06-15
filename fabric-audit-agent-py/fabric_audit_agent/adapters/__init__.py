"""Swappable I/O ports (adapters). Port of the Node ``adapters/*.js``.

The functional core (``pipeline.run_audit``) takes injected ports — dict-style
``{"collect": fn}`` / ``{"reason": fn}`` / ``{"deliver": fn}`` — so the same logic runs
offline (mock adapters, no external deps) or in production (real adapters).

Offline/mock adapters work with zero dependencies and back the test suite + local runs.
Production adapters (REST collector, Claude reasoner, Teams delivery) inject their
HTTP/LLM *client* so they stay unit-testable offline and swap to real SDKs at deploy;
concrete client builders live in ``adapters.clients`` (lazy-import msal/requests/anthropic).
"""
from ..reasoner_stub import create_stub_reasoner
from .collector_mock import create_mock_collector
from .delivery_file import create_file_delivery
from .store_local import create_local_store
from .lifecycle_store import create_lifecycle_store
from .ticketing import create_ticketing_delivery
from .collector_rest import create_rest_collector, fetch_all_pages
from .collector_activity import (
    create_activity_collector, fetch_activity_events, map_activity_event, fetch_log_analytics,
)
from .collector_csv import create_csv_collector, build_facts_from_files
from .collector_list_usages import create_list_usages_collector
from .collector_workspace_monitoring import create_workspace_monitoring_collector
from .collector_merge import create_merged_collector, merge_facts_list
from .reasoner_claude import create_claude_reasoner
from .delivery_teams import create_teams_delivery

__all__ = [
    "create_stub_reasoner",
    "create_mock_collector",
    "create_file_delivery",
    "create_local_store",
    "create_lifecycle_store",
    "create_ticketing_delivery",
    "create_rest_collector",
    "fetch_all_pages",
    "create_activity_collector",
    "fetch_activity_events",
    "map_activity_event",
    "fetch_log_analytics",
    "create_csv_collector",
    "build_facts_from_files",
    "create_list_usages_collector",
    "create_workspace_monitoring_collector",
    "create_merged_collector",
    "merge_facts_list",
    "create_claude_reasoner",
    "create_teams_delivery",
]
