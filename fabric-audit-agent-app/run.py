#!/usr/bin/env python3
"""Local CLI shim -> ``fabric_audit_agent.__main__``.

  python run.py audit | eval | whatif | triggers | lifecycle | dax | import | inspect | mytest

(Equivalent to ``python -m fabric_audit_agent ...``.) Run with no args for the command list.
The mock/offline path is 100% local — nothing leaves the machine. The production sweep runs
via ``fabric_audit_agent.job:main`` (Databricks wheel task) with real adapters.
"""
import sys

from fabric_audit_agent.__main__ import main

if __name__ == "__main__":
    main(sys.argv[1:])
