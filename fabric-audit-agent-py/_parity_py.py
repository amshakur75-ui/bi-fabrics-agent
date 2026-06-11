import json
import sys
from fabric_audit_agent.pipeline import run_audit
from fabric_audit_agent.reasoner_stub import create_stub_reasoner

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    facts = json.load(fh)

env = run_audit(
    {"collect": lambda: facts},
    create_stub_reasoner(),
    {"deliver": lambda e: None},
    agent_id="agent-parity",
    now="2026-06-11T00:00:00Z",
    tenant="Acme",
)

out = {
    "success": env["success"],
    "agent_id": env["agent_id"],
    "summary": env["summary"],
    "data": env["data"],
}
sys.stdout.write(json.dumps(out))
