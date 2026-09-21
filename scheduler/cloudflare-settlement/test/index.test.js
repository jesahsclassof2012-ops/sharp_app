import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import worker, { dispatchEndpoint, dispatchSettlementWorkflow } from "../src/index.js";

const env = {
  GITHUB_OWNER: "jesahsclassof2012-ops",
  GITHUB_REPO: "sharp_app",
  GITHUB_WORKFLOW: "settle-results.yml",
  GITHUB_REF: "main",
  GITHUB_TOKEN: "test-token-never-log",
};

function successfulFetch(calls) {
  return async (url, options) => {
    calls.push({ url, options });
    return { status: 200 };
  };
}

test("dispatches only the settlement workflow with GitHub's required request data", async () => {
  const calls = [];
  await dispatchSettlementWorkflow(env, successfulFetch(calls));

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "https://api.github.com/repos/jesahsclassof2012-ops/sharp_app/actions/workflows/settle-results.yml/dispatches",
  );
  assert.notEqual(calls[0].url.includes("collect-history.yml"), true);
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, '{"ref":"main"}');
  assert.deepEqual(calls[0].options.headers, {
    Accept: "application/vnd.github+json",
    Authorization: "Bearer test-token-never-log",
    "Content-Type": "application/json",
    "User-Agent": "sharp-app-settlement-scheduler",
    "X-GitHub-Api-Version": "2026-03-10",
  });
});

test("accepts only GitHub's documented successful dispatch status", async () => {
  await dispatchSettlementWorkflow(env, async () => ({ status: 200 }));
  await assert.rejects(
    () => dispatchSettlementWorkflow(env, async () => ({ status: 204 })),
    /HTTP 204/,
  );
});

test("non-success errors never include the GitHub token", async () => {
  await assert.rejects(
    () => dispatchSettlementWorkflow(env, async () => ({ status: 403 })),
    (error) => {
      assert.match(error.message, /HTTP 403/);
      assert.doesNotMatch(error.message, /test-token-never-log/);
      return true;
    },
  );
});

test("every required configuration value fails closed when missing", async () => {
  for (const name of Object.keys(env)) {
    const missing = { ...env };
    delete missing[name];
    await assert.rejects(
      () => dispatchSettlementWorkflow(missing, async () => ({ status: 200 })),
      new RegExp(`Missing required scheduler configuration: ${name}`),
    );
  }
});

test("scheduled handler makes exactly one dispatch attempt without retries", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = successfulFetch(calls);
  try {
    await worker.scheduled({}, env);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(calls.length, 1);
});

test("endpoint construction uses the configured repository identity", () => {
  assert.equal(
    dispatchEndpoint(env),
    "https://api.github.com/repos/jesahsclassof2012-ops/sharp_app/actions/workflows/settle-results.yml/dispatches",
  );
});

test("committed Worker configuration retains the isolated staged cron", async () => {
  const config = await readFile(new URL("../wrangler.toml", import.meta.url), "utf8");
  assert.match(config, /^name = "sharp-app-settlement-scheduler"$/m);
  assert.match(config, /^crons = \["47 \* \* \* \*"\]$/m);
  assert.match(config, /^GITHUB_WORKFLOW = "settle-results\.yml"$/m);
  assert.doesNotMatch(config, /collect-history\.yml/);
});
