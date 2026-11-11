#!/usr/bin/env bash
#
# Automated database backup for the Textile ERP.
#
# Backs up the database named by DATABASE_URL — PostgreSQL (pg_dump, custom
# format) or SQLite (consistent .backup) — to a timestamped file in BACKUP_DIR,
# then prunes backups older than RETENTION_DAYS. Idempotent and safe to run from
# cron; exits non-zero on any failure so a scheduler can alert.
#
# Env:
#   DATABASE_URL     required. e.g. postgresql+psycopg2://erp:pass@host:5432/erp
#                                   or sqlite:////var/lib/erp/erp.sqlite
#   BACKUP_DIR       where dumps are written (default: ./backups)
#   RETENTION_DAYS   prune dumps older than this many days (default: 30)
#
# Usage:  DATABASE_URL=... BACKUP_DIR=/var/backups/erp ./scripts/backup.sh
set -euo pipefail

DATABASE_URL="${DATABASE_URL:?DATABASE_URL must be set}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"
log() { echo "[backup $(date +%H:%M:%S)] $*"; }

case "$DATABASE_URL" in
  postgres*|postgresql*)
    # pg_dump understands libpq URLs, not SQLAlchemy's "+driver" suffix.
    PG_URL="${DATABASE_URL/+psycopg2/}"
    PG_URL="${PG_URL/+psycopg/}"
    OUT="$BACKUP_DIR/erp-$STAMP.dump"
    log "pg_dump -> $OUT"
    pg_dump --format=custom --no-owner --dbname "$PG_URL" --file "$OUT"
    ;;
  sqlite*)
    # Strip scheme to get the file path (sqlite:////abs or sqlite:///rel).
    DB_PATH="${DATABASE_URL#sqlite://}"; DB_PATH="/$(printf %s "$DB_PATH" | sed 's#^/*##')"
    OUT="$BACKUP_DIR/erp-$STAMP.sqlite"
    log "sqlite backup $DB_PATH -> $OUT"
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$DB_PATH" ".backup '$OUT'"      # consistent online backup
    else
      cp "$DB_PATH" "$OUT"                      # fallback: stop writers first
    fi
    ;;
  *)
    log "Unsupported DATABASE_URL scheme; nothing backed up." >&2
    exit 2
    ;;
esac

SIZE="$(du -h "$OUT" | cut -f1)"
log "wrote $OUT ($SIZE)"

log "pruning backups older than ${RETENTION_DAYS}d in $BACKUP_DIR"
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'erp-*' -mtime "+$RETENTION_DAYS" -print -delete || true

log "done. Current backups:"
ls -1t "$BACKUP_DIR"/erp-* 2>/dev/null | head -5 || true
