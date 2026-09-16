import assert from "node:assert/strict";
import test from "node:test";

import worker, { dispatchEndpoint, dispatchHistoryWorkflow } from "../src/index.js";

const env = {
  GITHUB_OWNER: "jesahsclassof2012-ops",
  GITHUB_REPO: "sharp_app",
  GITHUB_WORKFLOW: "collect-history.yml",
  GITHUB_REF: "main",
  GITHUB_TOKEN: "test-token-never-log",
};

function successfulFetch(calls) {
  return async (url, options) => {
    calls.push({ url, options });
    return { status: 200 };
  };
}

test("dispatches the documented GitHub workflow endpoint with required request data", async () => {
  const calls = [];
  await dispatchHistoryWorkflow(env, successfulFetch(calls));

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "https://api.github.com/repos/jesahsclassof2012-ops/sharp_app/actions/workflows/collect-history.yml/dispatches",
  );
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.body, '{"ref":"main"}');
  assert.deepEqual(calls[0].options.headers, {
    Accept: "application/vnd.github+json",
    Authorization: "Bearer test-token-never-log",
    "Content-Type": "application/json",
    "User-Agent": "sharp-app-history-scheduler",
    "X-GitHub-Api-Version": "2026-03-10",
  });
});

test("accepts only GitHub's documented successful dispatch status", async () => {
  await dispatchHistoryWorkflow(env, async () => ({ status: 200 }));
  await assert.rejects(
    () => dispatchHistoryWorkflow(env, async () => ({ status: 204 })),
    /HTTP 204/,
  );
});

test("non-success errors never include the GitHub token", async () => {
  await assert.rejects(
    () => dispatchHistoryWorkflow(env, async () => ({ status: 403 })),
    (error) => {
      assert.match(error.message, /HTTP 403/);
      assert.doesNotMatch(error.message, /test-token-never-log/);
      return true;
    },
  );
});

test("missing token and non-secret configuration fail safely", async () => {
  for (const name of Object.keys(env)) {
    const missing = { ...env };
    delete missing[name];
    await assert.rejects(
      () => dispatchHistoryWorkflow(missing, async () => ({ status: 200 })),
      new RegExp(`Missing required scheduler configuration: ${name}`),
    );
  }
});

test("scheduled handler dispatches exactly once without a custom retry loop", async () => {
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

test("endpoint construction uses configured repository identity", () => {
  assert.equal(dispatchEndpoint(env), "https://api.github.com/repos/jesahsclassof2012-ops/sharp_app/actions/workflows/collect-history.yml/dispatches");
});
