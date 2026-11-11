#!/usr/bin/env python
"""Deliberate backfill of historical operational rows to a Legacy/Unassigned factory.

SAFETY: this is a no-op PREVIEW by default. It only writes with --commit, and you
should BACK UP the database and run it on a disposable copy first.

Usage (from backend/):
    .venv/bin/python -m scripts.backfill_legacy_factory            # preview
    .venv/bin/python -m scripts.backfill_legacy_factory --commit   # apply
    .venv/bin/python -m scripts.backfill_legacy_factory --report   # reconciliation status
    .venv/bin/python -m scripts.backfill_legacy_factory --revert --commit  # undo

Requires migration 0004 (branches.is_legacy_unassigned) to be applied first.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.core.database import SessionLocal
from app.services.tenancy_backfill import (
    backfill_legacy_factory,
    reconciliation_report,
    revert_legacy_backfill,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", action="store_true", help="apply changes (default: preview only)")
    ap.add_argument("--report", action="store_true", help="print reconciliation status and exit")
    ap.add_argument("--revert", action="store_true", help="reverse the backfill instead of applying")
    args = ap.parse_args(argv)

    db = SessionLocal()
    try:
        if args.report:
            print(json.dumps(reconciliation_report(db), indent=2))
            return 0
        if args.revert:
            result = revert_legacy_backfill(db, commit=args.commit)
            print(json.dumps(result, indent=2))
        else:
            report = backfill_legacy_factory(db, commit=args.commit)
            print(json.dumps({
                "committed": report.committed,
                "total_assigned": report.total_assigned,
                "per_table_assigned": report.per_table_assigned,
                "legacy_factories": report.legacy_factories,
            }, indent=2))
        if not args.commit:
            print("\n(PREVIEW only — nothing was written. Re-run with --commit to apply.)",
                  file=sys.stderr)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
