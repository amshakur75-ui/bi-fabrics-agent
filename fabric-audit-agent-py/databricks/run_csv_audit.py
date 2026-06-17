# Databricks notebook source
# MAGIC %md
# MAGIC # Fabric Audit — CSV sweep (no permissions needed)
# MAGIC Reads a Capacity Metrics export from a Volume, runs the read-only audit, writes
# MAGIC `latest.json` + `report.md` back to the Volume, and optionally posts a Teams card.
# MAGIC No service principal or tenant settings required — only the exported CSV(s).

# COMMAND ----------

# MAGIC %pip install "/Volumes/main/default/wheels/fabric_audit_agent-1.0.0-py3-none-any.whl"
# MAGIC # add the [prod] extra if you set TEAMS_WEBHOOK_URL / ANTHROPIC_API_KEY:  ...whl[prod]

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
from fabric_audit_agent.job import run_csv_job

# Export from the Fabric Capacity Metrics app, upload to a Volume, then point here.
# Pass both data.csv (capacity timepoints) + Items.csv (per-item CU) to name top consumers.
csv_paths = [
    "/Volumes/main/default/fabric/data.csv",
    # "/Volumes/main/default/fabric/Items.csv",
]
out_dir = "/Volumes/main/default/fabric/out"

# Optional (uncomment + install ...whl[prod] above):
# os.environ["TEAMS_WEBHOOK_URL"] = dbutils.secrets.get("fabric-audit", "TEAMS_WEBHOOK_URL")
# os.environ["ANTHROPIC_API_KEY"] = dbutils.secrets.get("fabric-audit", "ANTHROPIC_API_KEY")

envelope = run_csv_job(csv_paths=csv_paths, out_dir=out_dir)
print(envelope["summary"])

# COMMAND ----------

# MAGIC %md ### The report
print(open(os.path.join(out_dir, "report.md"), encoding="utf-8").read())
