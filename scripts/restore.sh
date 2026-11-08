#!/usr/bin/env bash
#
# Restore a Textile ERP backup produced by scripts/backup.sh.
#
# PostgreSQL restores are directed at a SEPARATE target database by default, so
# you verify the restore before repointing the app — never overwrite the live
# DB by reflex. SQLite restores write the target file path you give.
#
# Env:
#   TARGET_URL   where to restore (default: a *_restore DB next to DATABASE_URL
#                for Postgres; required for SQLite).
#
# Usage:
#   # Postgres: restore into erp_restore (created if absent)
#   TARGET_URL=postgresql://erp:pass@host:5432/erp_restore \
#     ./scripts/restore.sh backups/erp-20261108-020000.dump
#
#   # SQLite: restore to a new file
#   TARGET_URL=sqlite:////var/lib/erp/erp_restore.sqlite \
#     ./scripts/restore.sh backups/erp-20261108-020000.sqlite
set -euo pipefail

DUMP="${1:?Usage: restore.sh <backup-file>}"
[ -f "$DUMP" ] || { echo "Backup file not found: $DUMP" >&2; exit 1; }
log() { echo "[restore $(date +%H:%M:%S)] $*"; }

case "$DUMP" in
  *.dump)
    TARGET_URL="${TARGET_URL:?Set TARGET_URL to a restore database (do not reuse the live DB)}"
    PG_URL="${TARGET_URL/+psycopg2/}"; PG_URL="${PG_URL/+psycopg/}"
    log "pg_restore $DUMP -> $PG_URL"
    # --clean so a re-run is idempotent; --no-owner for portability.
    pg_restore --no-owner --clean --if-exists --dbname "$PG_URL" "$DUMP"
    ;;
  *.sqlite)
    TARGET_URL="${TARGET_URL:?Set TARGET_URL to a sqlite target path}"
    DB_PATH="${TARGET_URL#sqlite://}"; DB_PATH="/$(printf %s "$DB_PATH" | sed 's#^/*##')"
    log "copying $DUMP -> $DB_PATH"
    cp "$DUMP" "$DB_PATH"
    ;;
  *)
    echo "Unknown backup type: $DUMP (expected .dump or .sqlite)" >&2; exit 2 ;;
esac

log "restore complete."
log "Verify: start the API against the restored DB, then open Reports →"
log "  Trial Balance (must balance), Stock vs GL Reconciliation, GST reconciliation."
