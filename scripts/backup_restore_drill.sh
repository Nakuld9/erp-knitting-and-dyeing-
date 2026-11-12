#!/usr/bin/env bash
#
# Database-per-company backup/restore DRILL (Phase 16, acceptance criterion 14).
#
# Proves, against real PostgreSQL, that a single company's operational database
# can be backed up and restored INDEPENDENTLY — without touching any other
# company's database — and that the restored accounting balances and inventory
# quantities match the backup point exactly.
#
# This is the per-company analogue of scripts/backup.sh + scripts/restore.sh:
# under database-per-company you fan those out over every company DSN plus the
# central/platform DSN. The platform (control) database holds global identity,
# memberships and the company->DSN registry; a company restore is only usable
# once the platform database is itself recoverable (see "recovery dependency"
# below).
#
# Env (all required; libpq URLs, NOT SQLAlchemy "+psycopg2" DSNs):
#   PGHOST / PGPORT   how to reach the server (socket dir or host + port)
#   CTL_DB            control/platform database name       (e.g. erp_ctl)
#   A_DB              company A operational database name   (e.g. erp_A)
#   B_DB              company B operational database name   (e.g. erp_B)
#   PSQL/PGADMIN      optional: a wrapper to run psql/pg_dump/etc as a DB admin
#                     (e.g. 'su postgres -c' on a dev box). Default: run directly.
#
# Usage (dev box, throwaway cluster on a unix socket):
#   PGHOST=/tmp/pgsock PGPORT=54331 CTL_DB=erp_ctl A_DB=erp_A B_DB=erp_B \
#     PGADMIN='su postgres -c' ./scripts/backup_restore_drill.sh
#
# Exits non-zero if the restore is not independent or the numbers do not match.
set -euo pipefail

: "${PGHOST:?set PGHOST (socket dir or host)}"
: "${PGPORT:?set PGPORT}"
: "${A_DB:?set A_DB}"; : "${B_DB:?set B_DB}"
PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
PGADMIN="${PGADMIN:-}"
WORK="${WORK:-$(mktemp -d)}"
# When a DB admin wrapper (e.g. 'su postgres -c') runs pg_dump as another OS
# user, that user must be able to write the dump file in WORK.
chmod 777 "$WORK" 2>/dev/null || true
DUMP="$WORK/${A_DB}.dump"

# Run a DB admin tool, optionally through a wrapper (e.g. 'su postgres -c').
adm() { if [ -n "$PGADMIN" ]; then $PGADMIN "$*"; else bash -c "$*"; fi; }
psql_a() { adm "$PGBIN/psql -p $PGPORT -h $PGHOST -U postgres -d $1 -v ON_ERROR_STOP=1 -tAc \"$2\""; }
fail() { echo "DRILL FAILED: $*" >&2; exit 1; }

echo "== seed balanced ledger + inventory into A ($A_DB) =="
CID=$(psql_a "$A_DB" "select gen_random_uuid()")
VID=$(psql_a "$A_DB" "select gen_random_uuid()")
psql_a "$A_DB" "set session_replication_role=replica;
  insert into vouchers(id,company_id,voucher_type_id,period_id,number,voucher_date,status,posting_reference,total_debit,total_credit,created_at,updated_at)
    values('$VID','$CID',gen_random_uuid(),gen_random_uuid(),'JV-DRILL',current_date,'POSTED','RESTORE-DRILL',10000,10000,now(),now());
  insert into journal_lines(id,voucher_id,line_no,account_id,debit,credit,created_at,updated_at)
    values(gen_random_uuid(),'$VID',1,gen_random_uuid(),10000,0,now(),now()),
           (gen_random_uuid(),'$VID',2,gen_random_uuid(),0,10000,now(),now());
  insert into inventory_valuation_layers(id,company_id,item_type,item_id,qty_in,qty_remaining,unit_cost,value_remaining,received_at,created_at,updated_at)
    values(gen_random_uuid(),'$CID','YARN',gen_random_uuid(),500,500,50,25000,now(),now(),now());"

D0=$(psql_a "$A_DB" "select coalesce(sum(debit),0) from journal_lines")
C0=$(psql_a "$A_DB" "select coalesce(sum(credit),0) from journal_lines")
Q0=$(psql_a "$A_DB" "select coalesce(sum(qty_remaining),0) from inventory_valuation_layers")
echo "   A at backup point: debit=$D0 credit=$C0 inv_qty=$Q0"

echo "== back up ONLY A =="
adm "$PGBIN/pg_dump -p $PGPORT -h $PGHOST -U postgres -Fc $A_DB -f $DUMP"

echo "== diverge A (unbalance + drain) AND change B =="
psql_a "$A_DB" "set session_replication_role=replica;
  insert into journal_lines(id,voucher_id,line_no,account_id,debit,credit,created_at,updated_at)
    values(gen_random_uuid(),'$VID',3,gen_random_uuid(),9999,0,now(),now());
  update inventory_valuation_layers set qty_remaining=0, value_remaining=0;"
B_BEFORE=$(psql_a "$B_DB" "select count(*) from organizations")
psql_a "$B_DB" "insert into organizations(id,code,name,is_active,created_at,updated_at) values(gen_random_uuid(),'B-SENTINEL','drill',true,now(),now())"

echo "== restore A INDEPENDENTLY (drop+create+pg_restore) — B is not touched =="
adm "$PGBIN/dropdb -p $PGPORT -h $PGHOST -U postgres $A_DB"
adm "$PGBIN/createdb -p $PGPORT -h $PGHOST -U postgres $A_DB"
adm "$PGBIN/pg_restore -p $PGPORT -h $PGHOST -U postgres -d $A_DB $DUMP" 2>&1 | tail -1 || true

echo "== verify restored balances + inventory, and B untouched =="
D1=$(psql_a "$A_DB" "select coalesce(sum(debit),0) from journal_lines")
C1=$(psql_a "$A_DB" "select coalesce(sum(credit),0) from journal_lines")
N1=$(psql_a "$A_DB" "select count(*) from journal_lines")
Q1=$(psql_a "$A_DB" "select coalesce(sum(qty_remaining),0) from inventory_valuation_layers")
V1=$(psql_a "$A_DB" "select coalesce(sum(value_remaining),0) from inventory_valuation_layers")
B_AFTER=$(psql_a "$B_DB" "select count(*) from organizations")
SENT=$(psql_a "$B_DB" "select count(*) from organizations where code='B-SENTINEL'")

echo "   A restored: debit=$D1 credit=$C1 lines=$N1 inv_qty=$Q1 inv_value=$V1"
echo "   B rows before change=$B_BEFORE after=$B_AFTER (B-SENTINEL present=$SENT)"

[ "$D1" = "$D0" ] && [ "$C1" = "$C0" ] || fail "ledger not restored to backup point ($D1/$C1 != $D0/$C0)"
[ "$D1" = "$C1" ] || fail "restored ledger does not balance ($D1 != $C1)"
[ "$N1" = "2" ] || fail "corruption line survived restore (lines=$N1, expected 2)"
[ "$Q1" = "$Q0" ] || fail "inventory qty not restored ($Q1 != $Q0)"
# B must still show the sentinel inserted AFTER A's backup — proving A's restore
# did not roll B back or overwrite it.
[ "$SENT" = "1" ] || fail "company B was affected by company A's restore"

echo "DRILL PASSED: A restored to its backup point (balanced, inventory intact); B independent."
rm -rf "$WORK"
