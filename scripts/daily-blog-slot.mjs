import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const MARKER_RELATIVE_PATH = join(".github", "daily-blog-scheduled-dates.txt");
const markerPath = () => join(process.cwd(), MARKER_RELATIVE_PATH);

function validateDate(date) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new Error(`Invalid scheduled date: ${date}`);
  }
  const parsed = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== date) {
    throw new Error(`Invalid scheduled date: ${date}`);
  }
}

function readDates() {
  try {
    return new Set(
      readFileSync(markerPath(), "utf8")
        .split(/\r?\n/)
        .filter(Boolean),
    );
  } catch (error) {
    if (error?.code === "ENOENT") return new Set();
    throw error;
  }
}

function writeOutputs(values) {
  const text = Object.entries(values)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n") + "\n";
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, text, "utf8");
  else process.stdout.write(text);
}

function check(date) {
  if (date === "") {
    writeOutputs({ scheduled: "false", should_publish: "true" });
    return;
  }
  validateDate(date);
  writeOutputs({
    scheduled: "true",
    should_publish: readDates().has(date) ? "false" : "true",
  });
}

function record(date) {
  validateDate(date);
  const dates = readDates();
  dates.add(date);
  const path = markerPath();
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${[...dates].sort().join("\n")}\n`, "utf8");
}

try {
  const [action, date = ""] = process.argv.slice(2);
  if (action === "check") check(date);
  else if (action === "record") record(date);
  else throw new Error(`Unknown action: ${action || "(blank)"}`);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
