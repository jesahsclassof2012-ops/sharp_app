# Sharp App external settlement scheduler (Phase 1)

This isolated Cloudflare Worker is **preparation only** for a staged external
settlement trigger. It is not deployed by this PR.

```text
Cloudflare hourly Cron -> GitHub workflow_dispatch -> settle-results.yml on main
-> python result_collector.py -> existing PostgreSQL results storage
```

The Worker never receives database credentials and does not access PostgreSQL.
Database credentials remain only in GitHub Actions.

## Staged configuration

Worker name: `sharp-app-settlement-scheduler`

The staged Cloudflare Cron is UTC and intentionally runs at minute 47:

```toml
[triggers]
crons = ["47 * * * *"]
```

The existing native GitHub settlement cron remains enabled at `17 * * * *`
during validation. The 30-minute offset avoids intentional same-minute overlap,
makes Cloudflare-dispatched `workflow_dispatch` runs distinguishable from native
`schedule` runs, and preserves GitHub as a temporary fallback. This project
makes no exact-delivery or SLA claim.

Committed non-secret Worker variables are:

| Variable | Value |
| --- | --- |
| `GITHUB_OWNER` | `jesahsclassof2012-ops` |
| `GITHUB_REPO` | `sharp_app` |
| `GITHUB_WORKFLOW` | `settle-results.yml` |
| `GITHUB_REF` | `main` |

`GITHUB_TOKEN` must be a Cloudflare Worker secret. For a later deployment, use
a fine-grained GitHub token restricted to the
`jesahsclassof2012-ops/sharp_app` repository with only **Actions: write**
permission. Do not grant database, Contents write, Administration, or
organization-wide permissions. Do not put the token in source,
`wrangler.toml`, examples, logs, or a committed local file.

## Phase 2 deployment procedure — document only

Do not perform these steps without separate approval.

1. Authenticate Wrangler to the intended Cloudflare account.
2. Create the repository-scoped fine-grained GitHub token described above.
3. Provide `GITHUB_TOKEN` securely as the Cloudflare Worker secret.
4. Deploy the reviewed Worker.
5. Verify the deployed Worker and its Cron Trigger.
6. Wait for a natural minute-47 cron invocation.
7. Verify the matching GitHub Actions run has `event=workflow_dispatch`,
   `head_branch=main`, and the expected main SHA.
8. Inspect the settlement workflow logs.
9. Observe several consecutive successful external hourly dispatches.
10. Only after separate approval, retire the native GitHub settlement cron.

## Local validation

Run tests without contacting GitHub:

```sh
pnpm test
```

Run the non-deploying Workers build validation:

```sh
pnpm exec wrangler deploy --dry-run --outdir .wrangler-dry-run
```

Do not run `wrangler deploy`, `wrangler secret put`, or any production
Cloudflare command as part of Phase 1.
