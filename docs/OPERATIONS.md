# Operations Runbook

Day-2 operations for the Textile ERP: what to schedule, what to watch, and what
to do when something breaks. For first-time install, configuration and upgrades
see `DEPLOYMENT.md`; for using the app see `USER_GUIDE.md`.

The one rule everything else serves: **the database is the only authoritative
state.** The container filesystem is disposable. Protect the database and you
can rebuild anything else.

---

## 1. Scheduled jobs

| Job | Script | Cadence | Notes |
|---|---|---|---|
| Database backup | `scripts/backup.sh` | nightly | Timestamped dump to `BACKUP_DIR`; prunes past `RETENTION_DAYS`. **Sync off-host after each run.** |
| Readiness probe | `scripts/healthcheck.sh` | every 5 min | Exit ≠ 0 ⇒ alert. Also the container `HEALTHCHECK`. |

`scripts/crontab.example` wires both up. Install with `crontab scripts/crontab.example`
after editing the paths and `DATABASE_URL` at the top.

Environment the jobs read: `DATABASE_URL`, `BACKUP_DIR` (default `./backups`),
`RETENTION_DAYS` (default `30`), `BASE_URL` (default `http://127.0.0.1:8000`).

---

## 2. Health & metrics

| Endpoint | Meaning | Use |
|---|---|---|
| `GET /health` | process is up (liveness) | restart probe |
| `GET /health/ready` | process **and** DB are up; `503` otherwise | load-balancer / orchestrator routing |
| `GET /health/metrics` | counters since start | scrape / dashboard |

`/health/metrics` returns:

```json
{
  "uptime_seconds": 10432.5,
  "requests_total": 48213,
  "requests_by_status_class": {"2xx": 47788, "4xx": 410, "5xx": 15},
  "errors_total": 15,
  "avg_duration_ms": 22.4,
  "max_duration_ms": 1890.0
}
```

Counters are in-process and reset on restart. For history, scrape on an interval
and store it; the durable business record lives in `audit_logs`, not here.

**Alert on:** `/health/ready` failing for >2 consecutive probes; `5xx` rate or
`errors_total` climbing; `avg_duration_ms` trending up (DB pressure); backup job
exit ≠ 0 or no new file in `BACKUP_DIR` for >24h.

---

## 3. Logs

One structured line per request, on stdout (capture via your container/log
stack):

```
2026-11-08 02:17:04 INFO  erp.access [9f1c2a3b4d5e6f70] method=POST path=/api/vouchers status=201 dur_ms=38.2
```

- **`X-Request-ID`** — on every response; an inbound one is honoured. The same
  id is in the log line and in the body of any `500`. Quote it in bug reports.
- **`LOG_LEVEL`** (default `INFO`) and **`LOG_JSON=true`** (one JSON object per
  line, for shippers) tune output.
- Unhandled exceptions log a full traceback under `erp.error` with the request
  id, and return a clean JSON `500` carrying that id — no stack traces leak to
  clients.

---

## 4. Incident playbook

**`/health/ready` returns 503 / app won't serve.**
1. `GET /health` — if that also fails the process is down; restart it, check
   stdout for a startup/migration error.
2. If `/health` is OK but `/health/ready` is 503, the database is unreachable:
   check the DB is up, `DATABASE_URL` is correct, and the network/credentials.
3. On Docker: `docker compose ps` (is `db` healthy?), `docker compose logs api`.

**Elevated 5xx.** Pull the `erp.error` logs, group by request id, read the
traceback. Most 5xx are a single failing path — fix forward; the ledger is
immutable so no bad data is committed by a failed request (posting is atomic).

**Slow responses.** Check `avg_duration_ms`/`max_duration_ms` and DB load. The
API is stateless — scale out with more uvicorn workers/replicas behind the load
balancer; the database is the shared bottleneck, so size and index it first.

**Suspected data issue.** The ledger self-checks: sign in and open **Reports →
Trial Balance** (must balance), **Stock vs GL Reconciliation** (must reconcile)
and the **GST reconciliation**. A clean run of the three means ledger and
sub-ledgers agree. Posted entries are immutable; correct by reversal, never by
editing history.

---

## 5. Recovery drill

A backup you have never restored is not a backup. Quarterly:

1. `scripts/restore.sh` the latest dump into a scratch database (`TARGET_URL`).
2. Start the API against it.
3. Run the three reconciliation reports above.
4. Record the restore time — that is your real RTO.

For a tighter RPO than nightly, enable PostgreSQL WAL archiving / PITR or use
your managed database's point-in-time recovery (`DEPLOYMENT.md` §6).

---

## 6. Routine administration

These are in-app, under the **Administration** and **Approvals** pages (admin
role). Fuller walkthroughs are in `USER_GUIDE.md`.

- **Users & roles** — create users, assign least-privilege roles; deactivate
  leavers (posted history stays attributed to them).
- **Period close** — close a month once reconciled; a closed period rejects new
  postings. Reopen only to correct, then re-close.
- **Approval rules** — set maker-checker thresholds per document type
  (`PAYMENT`, `PURCHASE_ORDER`, `VOUCHER`); at/above the threshold a document is
  held until approved by someone other than its maker.
- **Year-end** — the closing voucher zeroes income/expense into retained
  earnings and locks the year (idempotent). Back up first.
