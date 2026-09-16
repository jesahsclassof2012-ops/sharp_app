# Sharp App external history scheduler (Phase 1)

This isolated Cloudflare Worker is preparation for a staged migration of the
history-collection trigger only:

```text
Cloudflare Cron Trigger -> GitHub workflow_dispatch -> collect-history.yml on main
-> python history_collector.py -> existing PostgreSQL database
```

It does not have database credentials and does not call the collector directly.
The existing GitHub schedule remains enabled during Phases 1–3. This project is
not deployed by this PR.

## Configuration

The Worker cron is UTC and runs every 15 minutes:

```toml
[triggers]
crons = ["*/15 * * * *"]
```

Non-secret Worker variables are committed in `wrangler.toml`:

| Variable | Production value |
| --- | --- |
| `GITHUB_OWNER` | `jesahsclassof2012-ops` |
| `GITHUB_REPO` | `sharp_app` |
| `GITHUB_WORKFLOW` | `collect-history.yml` |
| `GITHUB_REF` | `main` |

`GITHUB_TOKEN` is required as a Cloudflare Worker secret and is never placed in
source, `wrangler.toml`, examples, or logs. The token should be a fine-grained
GitHub token restricted to the `jesahsclassof2012-ops/sharp_app` repository with
only **Actions: write** permission, which is the documented permission for
creating a workflow dispatch event.

## Phase 2 deployment steps — do not run as part of this PR

1. Authenticate Wrangler to the intended Cloudflare account.
2. Create the fine-grained GitHub token described above; do not grant database,
   contents, administration, or organization permissions.
3. From this directory, create the temporary local secrets file
   `.env.production` with dotenv syntax:

   ```sh
   GITHUB_TOKEN=<actual-token>
   ```

   `.env*` is gitignored. Keep this file local, never commit it, and never paste
   the actual token into this README or any source file.
4. Perform the reviewed initial deployment with the local secrets file:

   ```sh
   pnpm exec wrangler deploy --secrets-file .env.production
   ```

   Cloudflare documents that `--secrets-file` uploads dotenv or JSON secrets
   together with the Worker version. This one deployment applies the Worker
   source, `GITHUB_TOKEN`, committed non-secret GitHub variables,
   `workers_dev = false`, `preview_urls = false`, and the
   `*/15 * * * *` Cron Trigger from `wrangler.toml`.
5. After successful deployment, securely delete the local `.env.production`
   file (for example, `rm .env.production` in a POSIX shell).
6. Cloudflare documents that Cron Trigger changes can take several minutes, up
   to 15 minutes, to propagate. Verify it in Workers & Pages → the Worker →
   Settings → Triggers → Cron Triggers, or through Cloudflare's Worker schedules
   API.
7. After a trigger fires, find the GitHub Actions run for **Collect Sharp Signal
   history**. Confirm `event=workflow_dispatch`, `head_branch=main`, and the
   expected main SHA, then inspect the `Collect snapshots` log for
   `Inserted N snapshots.`

For later secret rotation after the Worker exists, Cloudflare documents
`pnpm exec wrangler secret put GITHUB_TOKEN` as a secret update command. It
creates a new Worker version and deploys it immediately, so use it only in an
approved, reviewed update—not as the initial Worker creation step.

## Rollback and migration safety

To stop the external trigger later, delete its Cron Trigger in the Cloudflare
dashboard or deploy a reviewed configuration with `crons = []`. Keep the native
GitHub cron enabled until multiple external dispatches have been verified in
production. A native cron event, an external dispatch, and a manual dispatch can
all create legitimate closely timed observations during the migration; this
project intentionally does not alter snapshot identity or deduplication.

## Reliability and testing

Cloudflare Cron Triggers execute in UTC but Cloudflare does not document an
exact-timing, delivery, or SLA guarantee here. Cron changes may take up to 15
minutes to propagate. The Worker makes one GitHub dispatch request per scheduled
invocation and has no custom retry loop; assess any platform retry/duplicate
behavior during Phase 2 monitoring.

Run local tests without contacting GitHub:

```sh
node --test test/*.test.js
```

Cloudflare documents this non-deploying build check:

```sh
pnpm exec wrangler deploy --dry-run --outdir .wrangler-dry-run
```

Do not use `wrangler deploy`, `wrangler secret put`, or any production Cloudflare
command until an explicit Phase 2 approval.
