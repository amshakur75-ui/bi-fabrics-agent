"""The agent brain: a raw Anthropic tool-loop over the read-only investigation tools.
Core (loop/prompt/adapter/investigator) is stdlib-only + offline-testable; the MLflow
ResponsesAgent wrapper and the real Databricks Claude client are import-guarded."""
