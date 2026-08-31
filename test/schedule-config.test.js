import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const workflow = readFileSync(new URL("../.github/workflows/daily-blog.yml", import.meta.url), "utf8");
const wrangler = JSON.parse(readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8"));

test("wrangler config contains exactly the two DST-covering cron expressions", () => {
  assert.deepEqual(wrangler.triggers?.crons, ["30 1 * * *", "30 2 * * *"]);
});

test("daily blog workflow has no GitHub schedule and keeps duplicate jobs serialized", () => {
  assert.doesNotMatch(workflow, /^\s+schedule:/m);
  assert.doesNotMatch(workflow, /^\s+- cron:/m);
  assert.match(workflow, /concurrency:\s*\n\s+group: daily-blog\s*\n\s+cancel-in-progress: false/);
});

test("daily blog workflow checks out current main before checking the scheduled slot", () => {
  const checkout = workflow.indexOf("name: Checkout");
  const nextStep = workflow.indexOf("\n      - name:", checkout);
  const markerGate = workflow.indexOf("node scripts/daily-blog-slot.mjs check");

  assert.ok(checkout >= 0 && nextStep > checkout && markerGate > nextStep);
  const checkoutStep = workflow.slice(checkout, nextStep);
  assert.match(checkoutStep, /uses: actions\/checkout@v4/);
  assert.match(checkoutStep, /ref: main/);
  assert.match(checkoutStep, /fetch-depth: 0/);
});

test("daily blog workflow wires scheduled-date gate, generation, marker, and commit in order", () => {
  assert.match(workflow, /^\s{6}scheduled_date:\s*$/m);
  assert.match(workflow, /id: slot/);
  assert.match(workflow, /SCHEDULED_DATE: \$\{\{ github\.event\.inputs\.scheduled_date \}\}/);
  assert.match(workflow, /node scripts\/daily-blog-slot\.mjs check "\$SCHEDULED_DATE"/);

  const generation = workflow.indexOf("name: Generate and publish blog post");
  const record = workflow.indexOf("name: Record scheduled publication slot");
  const commit = workflow.indexOf("name: Commit and push");
  assert.ok(generation >= 0 && record > generation && commit > record);

  assert.match(
    workflow,
    /name: Generate and publish blog post\s*\n\s+if: steps\.slot\.outputs\.should_publish == 'true'/,
  );
  assert.match(
    workflow,
    /name: Record scheduled publication slot\s*\n\s+if: steps\.slot\.outputs\.scheduled == 'true' && steps\.slot\.outputs\.should_publish == 'true'/,
  );
  assert.match(workflow, /node scripts\/daily-blog-slot\.mjs record "\$SCHEDULED_DATE"/);
  assert.match(
    workflow,
    /name: Commit and push\s*\n\s+if: steps\.slot\.outputs\.should_publish == 'true'/,
  );
});
