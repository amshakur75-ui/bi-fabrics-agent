"""Local-JSON StorePort: persists run history. Port of ``adapters/store.local.js``.

The prod store (Delta/Unity Catalog at deploy) implements the same ``{history, append}``
contract against a real table. ``keep`` trims to the most recent N runs.
"""
import json
import os


def create_local_store(file_path, keep=180):
    def history():
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return []

    def append(run):
        all_runs = history()
        all_runs.append(run)
        trimmed = all_runs[-keep:]
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as fh:
            json.dump(trimmed, fh, indent=2, ensure_ascii=False)
        return len(trimmed)

    return {"history": history, "append": append}
