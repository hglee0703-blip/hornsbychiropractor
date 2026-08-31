import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import worker from "../worker.js";

const DISPATCH_URL =
  "https://api.github.com/repos/hglee0703-blip/hornsbychiropractor/actions/workflows/daily-blog.yml/dispatches";
const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function invokeScheduled(
  scheduledTime,
  env = { GITHUB_TOKEN: "test-token" },
  dependencies,
) {
  const tracked = [];
  const ctx = {
    waitUntil(promise) {
      tracked.push(promise);
    },
  };

  const result = worker.scheduled({ scheduledTime }, env, ctx, dependencies);
  assert.equal(result, undefined);
  return tracked;
}

async function runDispatch(scheduledTime, env, dependencies) {
  const tracked = invokeScheduled(scheduledTime, env, dependencies);
  assert.equal(tracked.length, 1, "scheduled dispatch must be tracked with ctx.waitUntil");
  await tracked[0];
}

function responseWithTrackedBody(status, bodyContent) {
  const encoder = new TextEncoder();
  const bodyChunks = Array.isArray(bodyContent) ? bodyContent : [bodyContent];
  let cancelled = 0;
  let reads = 0;
  const response = {
    status,
    body: new ReadableStream({
      start(controller) {
        for (const chunk of bodyChunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
      pull() {
        reads += 1;
      },
      cancel() {
        cancelled += 1;
      },
    }),
    async text() {
      assert.fail("dispatch errors must not read response.text()");
    },
  };
  return {
    response,
    cancelled: () => cancelled,
    reads: () => reads,
  };
}

function fastDependencies(fetchImpl, sleeps) {
  return {
    fetch: fetchImpl,
    sleep: async (milliseconds) => {
      sleeps.push(milliseconds);
    },
  };
}

test("dispatches the exact blog workflow request at 12:30 Sydney time during AEST", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(null, { status: 204 });
  };

  await runDispatch(Date.parse("2026-06-01T02:30:00Z"), { GITHUB_TOKEN: "aest-token" });

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, DISPATCH_URL);
  assert.deepEqual(requests[0].options, {
    method: "POST",
    headers: {
      Authorization: "Bearer aest-token",
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
      "User-Agent": "hornsbychiropractor-cloudflare-worker",
    },
    body: JSON.stringify({
      ref: "main",
      inputs: { force: "true", scheduled_date: "2026-06-01" },
    }),
  });
});

test("dispatches at 12:30 Sydney time during AEDT", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(null, { status: 204 });
  };

  await runDispatch(Date.parse("2026-01-15T01:30:00Z"));

  assert.equal(calls, 1);
});

test("duplicate deliveries for one scheduled event send the same Sydney date key", async () => {
  const dateKeys = [];
  globalThis.fetch = async (_url, options) => {
    dateKeys.push(JSON.parse(options.body).inputs.scheduled_date);
    return new Response(null, { status: 204 });
  };
  const scheduledTime = Date.parse("2026-01-15T01:30:00Z");

  await runDispatch(scheduledTime);
  await runDispatch(scheduledTime);

  assert.deepEqual(dateKeys, ["2026-01-15", "2026-01-15"]);
});

test("derives the Sydney date and exact eligibility adjacent to both DST transitions", async () => {
  const cases = [
    ["2026-04-04T01:30:00Z", "2026-04-04"],
    ["2026-04-05T02:30:00Z", "2026-04-05"],
    ["2026-10-03T02:30:00Z", "2026-10-03"],
    ["2026-10-04T01:30:00Z", "2026-10-04"],
  ];
  const dateKeys = [];
  globalThis.fetch = async (_url, options) => {
    dateKeys.push(JSON.parse(options.body).inputs.scheduled_date);
    return new Response(null, { status: 204 });
  };

  for (const [timestamp] of cases) await runDispatch(Date.parse(timestamp));

  assert.deepEqual(dateKeys, cases.map(([, date]) => date));
});

test("skips the nonmatching UTC occurrence in both Sydney offset seasons", () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(null, { status: 204 });
  };

  const aestTracked = invokeScheduled(Date.parse("2026-06-01T01:30:00Z"));
  const aedtTracked = invokeScheduled(Date.parse("2026-01-15T02:30:00Z"));

  assert.equal(aestTracked.length, 0);
  assert.equal(aedtTracked.length, 0);
  assert.equal(calls, 0);
});

test("rejects a matching dispatch when GITHUB_TOKEN is missing", async () => {
  globalThis.fetch = async () => {
    assert.fail("fetch must not be called without GITHUB_TOKEN");
  };

  const tracked = invokeScheduled(Date.parse("2026-06-01T02:30:00Z"), {});
  assert.equal(tracked.length, 1);
  await assert.rejects(tracked[0], /GITHUB_TOKEN/);
});

test("omits a failed response body even when truncation would split the token", async () => {
  const token = "super-secret-token";
  const boundaryChunks = [`${"x".repeat(495)}sup`, "er-secret-token must never appear"];
  const failed = responseWithTrackedBody(403, boundaryChunks);
  const sleeps = [];
  const fetchImpl = async () => failed.response;

  const tracked = invokeScheduled(
    Date.parse("2026-06-01T02:30:00Z"),
    { GITHUB_TOKEN: token },
    fastDependencies(fetchImpl, sleeps),
  );
  assert.equal(tracked.length, 1);
  await assert.rejects(tracked[0], (error) => {
    assert.equal(error.message, "GitHub workflow dispatch failed with status 403 after 1 attempt");
    assert.doesNotMatch(error.message, /x{20}|super|secret|token|must never appear/);
    return true;
  });
  assert.equal(failed.cancelled(), 1, "the unread response body must be cancelled");
  assert.equal(failed.reads(), 0, "the response body must not be read");
  assert.deepEqual(sleeps, []);
});

test("retries a 429 response with bounded backoff and succeeds", async () => {
  const failed = responseWithTrackedBody(429, "rate limited test-token");
  const responses = [failed.response, { status: 204, body: null }];
  const sleeps = [];
  let attempts = 0;
  const fetchImpl = async () => responses[attempts++];

  await runDispatch(
    Date.parse("2026-06-01T02:30:00Z"),
    { GITHUB_TOKEN: "test-token" },
    fastDependencies(fetchImpl, sleeps),
  );

  assert.equal(attempts, 2);
  assert.deepEqual(sleeps, [1_000]);
  assert.equal(failed.cancelled(), 1);
  assert.equal(failed.reads(), 0);
});

test("retries 5xx responses with bounded exponential backoff and succeeds", async () => {
  const first = responseWithTrackedBody(500, "first failure test-token");
  const second = responseWithTrackedBody(503, "second failure test-token");
  const responses = [first.response, second.response, { status: 204, body: null }];
  const sleeps = [];
  let attempts = 0;
  const fetchImpl = async () => responses[attempts++];

  await runDispatch(
    Date.parse("2026-06-01T02:30:00Z"),
    { GITHUB_TOKEN: "test-token" },
    fastDependencies(fetchImpl, sleeps),
  );

  assert.equal(attempts, 3);
  assert.deepEqual(sleeps, [1_000, 2_000]);
  assert.deepEqual([first.cancelled(), second.cancelled()], [1, 1]);
  assert.deepEqual([first.reads(), second.reads()], [0, 0]);
});

test("retries a network exception and succeeds", async () => {
  const sleeps = [];
  let attempts = 0;
  const fetchImpl = async () => {
    attempts += 1;
    if (attempts === 1) throw new TypeError("socket failed with test-token in diagnostics");
    return { status: 204, body: null };
  };

  await runDispatch(
    Date.parse("2026-06-01T02:30:00Z"),
    { GITHUB_TOKEN: "test-token" },
    fastDependencies(fetchImpl, sleeps),
  );

  assert.equal(attempts, 2);
  assert.deepEqual(sleeps, [1_000]);
});

test("fails immediately for non-retryable 401 and 403 responses", async (t) => {
  for (const status of [401, 403]) {
    await t.test(String(status), async () => {
      const failed = responseWithTrackedBody(status, `denied with test-token at ${status}`);
      const sleeps = [];
      let attempts = 0;
      const fetchImpl = async () => {
        attempts += 1;
        return failed.response;
      };

      const tracked = invokeScheduled(
        Date.parse("2026-06-01T02:30:00Z"),
        { GITHUB_TOKEN: "test-token" },
        fastDependencies(fetchImpl, sleeps),
      );
      await assert.rejects(tracked[0], (error) => {
        assert.equal(
          error.message,
          `GitHub workflow dispatch failed with status ${status} after 1 attempt`,
        );
        assert.doesNotMatch(error.message, /denied|test-token/);
        return true;
      });
      assert.equal(attempts, 1);
      assert.deepEqual(sleeps, []);
      assert.equal(failed.cancelled(), 1);
      assert.equal(failed.reads(), 0);
    });
  }
});

test("stops retryable HTTP failures after exactly three attempts without leaking bodies", async () => {
  const token = "test-token";
  const failures = [500, 502, 503].map((status) =>
    responseWithTrackedBody(status, `private response ${token} for ${status}`),
  );
  const sleeps = [];
  let attempts = 0;
  const fetchImpl = async () => failures[attempts++].response;

  const tracked = invokeScheduled(
    Date.parse("2026-06-01T02:30:00Z"),
    { GITHUB_TOKEN: token },
    fastDependencies(fetchImpl, sleeps),
  );
  await assert.rejects(tracked[0], (error) => {
    assert.equal(error.message, "GitHub workflow dispatch failed with status 503 after 3 attempts");
    assert.doesNotMatch(error.message, /private response|test-token/);
    return true;
  });
  assert.equal(attempts, 3);
  assert.deepEqual(sleeps, [1_000, 2_000]);
  assert.deepEqual(failures.map(({ cancelled }) => cancelled()), [1, 1, 1]);
  assert.deepEqual(failures.map(({ reads }) => reads()), [0, 0, 0]);
});

test("stops network failures after exactly three attempts with a generic safe error", async () => {
  const token = "test-token";
  const sleeps = [];
  let attempts = 0;
  const fetchImpl = async () => {
    attempts += 1;
    throw new TypeError(`network diagnostics include ${token}`);
  };

  const tracked = invokeScheduled(
    Date.parse("2026-06-01T02:30:00Z"),
    { GITHUB_TOKEN: token },
    fastDependencies(fetchImpl, sleeps),
  );
  await assert.rejects(tracked[0], (error) => {
    assert.equal(error.message, "GitHub workflow dispatch failed after 3 network attempts");
    assert.doesNotMatch(error.message, /network diagnostics|test-token/);
    return true;
  });
  assert.equal(attempts, 3);
  assert.deepEqual(sleeps, [1_000, 2_000]);
});
