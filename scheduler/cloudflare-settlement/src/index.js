const REQUIRED_CONFIG = [
  "GITHUB_OWNER",
  "GITHUB_REPO",
  "GITHUB_WORKFLOW",
  "GITHUB_REF",
  "GITHUB_TOKEN",
];

const GITHUB_API_VERSION = "2026-03-10";

function requiredValue(env, name) {
  const value = env?.[name];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Missing required scheduler configuration: ${name}`);
  }
  return value;
}

function configuration(env) {
  return Object.fromEntries(
    REQUIRED_CONFIG.map((name) => [name, requiredValue(env, name)]),
  );
}

export function dispatchEndpoint(env) {
  const { GITHUB_OWNER, GITHUB_REPO, GITHUB_WORKFLOW } = configuration(env);
  return `https://api.github.com/repos/${encodeURIComponent(GITHUB_OWNER)}/${encodeURIComponent(GITHUB_REPO)}/actions/workflows/${encodeURIComponent(GITHUB_WORKFLOW)}/dispatches`;
}

export async function dispatchSettlementWorkflow(env, fetchImpl = fetch) {
  const config = configuration(env);
  const response = await fetchImpl(dispatchEndpoint(config), {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${config.GITHUB_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "sharp-app-settlement-scheduler",
      "X-GitHub-Api-Version": GITHUB_API_VERSION,
    },
    body: JSON.stringify({ ref: config.GITHUB_REF }),
  });

  // GitHub documents 200 as the successful Create a workflow dispatch event response.
  if (response.status !== 200) {
    throw new Error(`GitHub workflow dispatch failed with HTTP ${response.status}`);
  }
}

export default {
  async scheduled(_controller, env) {
    // Exactly one dispatch attempt per scheduled invocation; no custom retry loop.
    await dispatchSettlementWorkflow(env);
  },
};
