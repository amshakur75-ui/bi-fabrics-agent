"""Lifecycle-state StorePort (key -> state record). Port of ``adapters/lifecycle.store.js``.

Persists the per-finding lifecycle map (snooze/ack/resolve). Prod-DB at deploy.
"""
import json
import os


def create_lifecycle_store(file_path):
    def load():
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}

    def save(states):
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as fh:
            json.dump(states, fh, indent=2, ensure_ascii=False)
        return states

    return {"load": load, "save": save}
