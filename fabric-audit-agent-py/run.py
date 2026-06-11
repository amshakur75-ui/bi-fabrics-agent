#!/usr/bin/env python3
"""Local CLI for the Python Fabric/Power BI audit agent.

  python run.py import <file> [moreFiles...]   import CSV/.vpax exports + run the diagnosis
  python run.py inspect <file.csv>             safe per-column stats (no sensitive values)
  python run.py mytest                         re-run the diagnosis on my-estate.json

100% local: nothing leaves this machine, no network, no API key.
"""
import sys

from fabric_audit_agent.cli import run_import, run_mytest


def main(argv):
    if not argv:
        print(run_import([]))  # prints usage
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "import":
        print(run_import(rest))
    elif cmd == "inspect":
        print(run_import(rest, inspect=True))
    elif cmd == "mytest":
        print(run_mytest())
    else:
        print(run_import(argv))  # be forgiving: treat bare args as files to import


if __name__ == "__main__":
    main(sys.argv[1:])
