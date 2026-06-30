"""CLI dispatcher: ``python -m fabric_audit_agent <command> [args...]``.

  audit                           full pipeline (mock adapters) -> runs/latest.json + report.md
  eval                            score the golden suite
  whatif <kind> <sizeGB> <at>     capacity what-if (e.g. whatif model 5 06:00)
  triggers                        evaluate immediate triggers
  lifecycle <action> <key> [...]  set a finding's lifecycle state (snoozed needs an ISO date)
  dax "<measure>"                 DAX anti-pattern analysis
  import <file> [...]             import CSV/.vpax exports + diagnose
  inspect <file.csv>              safe per-column stats
  mytest                          re-diagnose my-estate.json
"""
import sys
from datetime import datetime, timezone

from .cli import run_import, run_mytest
from . import entrypoints as ep


def main(argv=None):
    # UTF-8 stdout so the em-dash/arrow glyphs in findings print on a stock Windows console
    # (cp1252) instead of crashing with UnicodeEncodeError. Linux/Databricks: effectively a no-op.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(run_import([]))   # prints usage
        return
    cmd, rest = argv[0], argv[1:]

    if cmd == "audit":
        print(ep.run_audit_cli())
    elif cmd == "eval":
        print(ep.run_eval_cli())
    elif cmd == "whatif":
        kind = rest[0] if len(rest) > 0 else None
        try:
            size_gb = float(rest[1]) if len(rest) > 1 else 0
        except ValueError:
            size_gb = 0          # Number(x) || 0
        refresh_at = rest[2] if len(rest) > 2 else None
        print(ep.run_whatif_cli(kind, size_gb, refresh_at))
    elif cmd == "triggers":
        print(ep.run_triggers_cli())
    elif cmd == "lifecycle":
        action = rest[0] if len(rest) > 0 else None
        key = rest[1] if len(rest) > 1 else None
        extra = rest[2:]
        snooze_until = None
        if action == "snoozed":
            snooze_until = extra[0] if extra else None
            note = " ".join(extra[1:]) or None
        else:
            note = " ".join(extra) or None
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        print(ep.run_lifecycle_cli(action, key, snooze_until=snooze_until, note=note, now=now))
    elif cmd == "dax":
        print(ep.run_dax_cli(" ".join(rest)))
    elif cmd == "import":
        print(run_import(rest))
    elif cmd == "inspect":
        print(run_import(rest, inspect=True))
    elif cmd == "mytest":
        print(run_mytest())
    elif cmd == "eval-investigations":
        from .eval.score_investigations import run_suite
        res = run_suite()
        print(f"Investigations: {res['passed']}/{res['total']} passed")
        for c in res["cases"]:
            print(f"  {'PASS' if c['passed'] else 'FAIL'} {c['name']} (abstain={c['abstainOk']} grounded={c['groundedOk']})")
        return
    else:
        print(run_import(argv))   # forgiving: treat bare args as files to import


def _console():
    """console_scripts entry (zero-arg)."""
    main(sys.argv[1:])


if __name__ == "__main__":
    main()
