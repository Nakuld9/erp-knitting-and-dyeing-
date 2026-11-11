#!/usr/bin/env python
"""Provision a dedicated database for a company (database-per-company).

PostgreSQL: create the empty database first (as a DB admin), e.g.
    createdb erp_company_acme
then run:
    .venv/bin/python -m scripts.provision_company_db \
        --company-id <uuid> \
        --dsn "postgresql+psycopg2://erp:***@HOST:5432/erp_company_acme"

This runs the full migrations against the DSN, seeds the company's company-scoped
data in that database, and registers the mapping in the control registry. BACK UP
first and test against a disposable database copy.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid

from app.services.company_provisioning import ProvisioningError, provision_company_database


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company-id", required=True)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--fy-start-year", type=int, default=2026)
    ap.add_argument("--no-register", action="store_true", help="migrate+seed but do not route to it yet")
    args = ap.parse_args(argv)
    try:
        cid = uuid.UUID(args.company_id)
    except ValueError:
        print("Invalid --company-id (must be a UUID)", file=sys.stderr)
        return 2
    try:
        result = provision_company_database(cid, args.dsn, fy_start_year=args.fy_start_year,
                                            register=not args.no_register)
    except ProvisioningError as e:
        print(f"Provisioning failed: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
