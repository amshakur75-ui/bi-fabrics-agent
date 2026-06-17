# Databricks notebook source
# MAGIC %md
# MAGIC # Fabric Audit — CSV sweep (no permissions needed)
# MAGIC Reads a Capacity Metrics export from a Volume, runs the read-only audit, writes
# MAGIC `latest.json` + `report.md` back to the Volume, and optionally posts a Teams card.
# MAGIC No service principal or tenant settings required — only the exported CSV(s).

# COMMAND ----------

# MAGIC %md
# MAGIC **Install** — pick ONE. Quickest for testing is installing straight from the cloned Git folder.

# COMMAND ----------

# MAGIC # A) Quick test: install from this cloned repo (needs cluster internet for build deps).
# MAGIC #    Replace <you> with your workspace user/repo path (see the Git folder location).
# MAGIC %pip install /Workspace/Users/<you>/bi-fabrics-agent/fabric-audit-agent-py
# MAGIC
# MAGIC # B) Production: install a pre-built wheel uploaded to a Volume (works on offline clusters).
# MAGIC # %pip install "/Volumes/main/default/wheels/fabric_audit_agent-1.0.0-py3-none-any.whl"
# MAGIC #
# MAGIC # For Teams/Claude add the [prod] extra:  .../fabric-audit-agent-py[prod]  or  ...whl[prod]

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
from fabric_audit_agent.job import run_csv_job

# Export from the Fabric Capacity Metrics app, upload to a Volume, then point here.
# Pass both data.csv (capacity timepoints) + Items.csv (per-item CU) to name top consumers.
csv_paths = [
    "/Volumes/main/bi_fabrics_agent/raw/capacity_metrics/data.csv",
    # "/Volumes/main/bi_fabrics_agent/raw/capacity_metrics/Items.csv",
]
out_dir = "/Volumes/main/bi_fabrics_agent/reports/current"

# Optional — richer reasoning via an in-tenant Databricks-hosted Claude model.
# Confirm the endpoint name under Serving / the AI Playground, and `%pip install openai` above.
# os.environ["DATABRICKS_CLAUDE_ENDPOINT"] = "databricks-claude-3-7-sonnet"
#
# Optional — Teams push / external Anthropic key instead (uncomment + install ...[prod] above):
# os.environ["TEAMS_WEBHOOK_URL"] = dbutils.secrets.get("fabric-audit", "TEAMS_WEBHOOK_URL")
# os.environ["ANTHROPIC_API_KEY"] = dbutils.secrets.get("fabric-audit", "ANTHROPIC_API_KEY")

envelope = run_csv_job(csv_paths=csv_paths, out_dir=out_dir)
print(envelope["summary"])

# COMMAND ----------

# MAGIC %md ### The report
print(open(os.path.join(out_dir, "report.md"), encoding="utf-8").read())
