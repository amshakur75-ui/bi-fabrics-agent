"""Mock CollectorPort: reads facts from a fixture JSON file. Port of ``adapters/collector.mock.js``.

The real collector (``collector_rest``) calls Fabric/Power BI/Azure APIs and emits the same
fact shape; this one just loads a fixture so the pipeline runs fully offline.
"""
import json


def create_mock_collector(fixture_path):
    def collect():
        with open(fixture_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    return {"collect": collect}
