# sharp_app
Sharp betting analysis
# Sharp Signal V2

## Phase 2 history collection

Run `python history_collector.py` to write persistent snapshots. It uses
`DATABASE_URL` when supplied (PostgreSQL in production). For local
development/testing, explicitly pass a SQLite URL such as `sqlite:///history.db`
to `HistoryStore`; deployed Streamlit never silently creates a SQLite database.

The included GitHub Actions workflow runs every 15 minutes and requires a
repository secret named `DATABASE_URL` containing a PostgreSQL connection URL.
It intentionally fails in Actions when that secret is absent so production
history is never silently written to ephemeral runner storage.

Configure the same `DATABASE_URL` in both GitHub Actions repository secrets and
Streamlit Community Cloud app secrets/environment. In deployed Streamlit, the
History section shows a configuration warning when it is absent; the live
scanner remains available. SQLite fallback is only for local development.

## Automated result ingestion

`result_collector.py` settles results from ESPN's full date-scoped scoreboard:
`https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=YYYYMMDD&limit=500` and, for NCAAF, the explicit union of
`.../college-football/scoreboard?dates=YYYYMMDD&limit=500&groups=80` (FBS)
and `...&groups=81` (FCS). The collector requests the stored UTC date plus adjacent
dates, then deduplicates provider events by `(sport, external_event_id)`.
Conflicting duplicate copies fail the run rather than choosing a result.

The hourly **Settle Sharp Signal results** workflow uses the same
`DATABASE_URL` secret as the history collector and Streamlit History UI.
GitHub cron may run late. Missing/malformed provider responses fail the run;
they are never interpreted as an empty slate.

Only explicit ESPN `final` events are recorded. The collector finds unresolved
past games and rechecks settled games for 48 hours. It matches sport, explicit
away/home identities, and kickoff within 12 hours; it never swaps sides,
fuzzy-matches schools, or guesses ambiguous/missing identity. The canonical
Sharp Signal `game_key` remains authoritative; ESPN's event ID is provenance
metadata (`result_source`, `external_event_id`, `source_status`, and
`result_fetched_at_utc`). An existing provider ID is never silently changed.

Each game keeps one result row. Rechecks preserve the original
`settled_at_utc`; an explicit corrected final updates its scores and fetch
timestamp. The captured entry snapshot line determines grading, while a later
valid pregame closing snapshot determines CLV. Final scores do not determine
CLV.

Automated settlement supports **NFL and NCAAF only**. The hourly workflow
checks unresolved games from the prior 14 days and separately rechecks settled
results for corrected finals during the following 48 hours. Older unresolved
games remain stored and can be recovered with a manual, safety-equivalent
backfill, for example: `python result_collector.py --backfill-days 120`.
Backfills retain final-only settlement, deterministic team matching, ambiguity
rejection, provider failure propagation, and duplicate-event protection.
