# sharp_app
Sharp betting analysis
# Sharp Signal V2

## Phase 2 history collection

Run `python history_collector.py` to write persistent snapshots. It uses
`DATABASE_URL` when supplied (PostgreSQL in production) and otherwise creates
the local SQLite database `sharp_signal_history.db` for development/testing.

The included GitHub Actions workflow runs every 15 minutes and requires a
repository secret named `DATABASE_URL` containing a PostgreSQL connection URL.
It intentionally fails in Actions when that secret is absent so production
history is never silently written to ephemeral runner storage.

Configure the same `DATABASE_URL` in both GitHub Actions repository secrets and
Streamlit Community Cloud app secrets/environment. In deployed Streamlit, the
History section shows a configuration warning when it is absent; the live
scanner remains available. SQLite fallback is only for local development.

Results are recorded through `HistoryStore.record_result(...)` using actual
captured snapshot lines. Automated score ingestion is intentionally not enabled
yet because this repository has no verified results-data source configured.
