import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, test } from "node:test";
import { spawnSync } from "node:child_process";

const CLI = fileURLToPath(new URL("../scripts/daily-blog-slot.mjs", import.meta.url));
const MARKER_RELATIVE_PATH = join(".github", "daily-blog-scheduled-dates.txt");
const roots = [];

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

function makeRoot(marker = null) {
  const root = mkdtempSync(join(tmpdir(), "daily-blog-slot-"));
  roots.push(root);
  if (marker !== null) {
    mkdirSync(join(root, ".github"), { recursive: true });
    writeFileSync(join(root, MARKER_RELATIVE_PATH), marker, "utf8");
  }
  return root;
}

function runCli(root, action, date) {
  const outputPath = join(root, "github-output.txt");
  const result = spawnSync(process.execPath, [CLI, action, date], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, GITHUB_OUTPUT: outputPath },
  });
  const output = result.status === 0 && existsSync(outputPath) ? readFileSync(outputPath, "utf8") : "";
  return { ...result, output };
}

test("a scheduled date already in the durable marker is skipped", () => {
  const root = makeRoot("2026-05-31\n2026-06-01\n");

  const result = runCli(root, "check", "2026-06-01");

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.output, /^scheduled=true$/m);
  assert.match(result.output, /^should_publish=false$/m);
  assert.equal(readFileSync(join(root, MARKER_RELATIVE_PATH), "utf8"), "2026-05-31\n2026-06-01\n");
});

test("a new scheduled date is eligible without changing the marker during the check", () => {
  const root = makeRoot("2026-05-31\n");

  const result = runCli(root, "check", "2026-06-01");

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.output, /^scheduled=true$/m);
  assert.match(result.output, /^should_publish=true$/m);
  assert.equal(readFileSync(join(root, MARKER_RELATIVE_PATH), "utf8"), "2026-05-31\n");
});

test("a blank manual date remains eligible and never changes the scheduled marker", () => {
  const root = makeRoot("2026-05-31\n");

  const result = runCli(root, "check", "");

  assert.equal(result.status, 0, result.stderr);
  assert.match(result.output, /^scheduled=false$/m);
  assert.match(result.output, /^should_publish=true$/m);
  assert.equal(readFileSync(join(root, MARKER_RELATIVE_PATH), "utf8"), "2026-05-31\n");
});

test("record durably adds a scheduled date once at the fixed repository marker path", () => {
  const root = makeRoot("2026-05-31\n");

  const first = runCli(root, "record", "2026-06-01");
  const duplicate = runCli(root, "record", "2026-06-01");

  assert.equal(first.status, 0, first.stderr);
  assert.equal(duplicate.status, 0, duplicate.stderr);
  assert.equal(
    readFileSync(join(root, MARKER_RELATIVE_PATH), "utf8"),
    "2026-05-31\n2026-06-01\n",
  );
});

test("malformed and impossible scheduled dates fail closed", () => {
  for (const date of ["2026-6-01", "2026-02-30", "../2026-06-01", "not-a-date"]) {
    const root = makeRoot();
    const result = runCli(root, "check", date);
    assert.notEqual(result.status, 0, `${date} must be rejected`);
    assert.match(result.stderr, /Invalid scheduled date/);
  }
});
