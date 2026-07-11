#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const {
  detectRuntime,
  shouldDisplayUpdate,
  updateInstruction,
} = require("../hooks/check-update.js");
const {
  ONE_DAY,
  cachePathForVersion,
  isFreshCacheForVersion,
  isNewer,
  readInstalledVersion,
} = require("../hooks/check-update-worker.js");

const repoRoot = path.resolve(__dirname, "..");
const repoVersion = fs.readFileSync(path.join(repoRoot, "VERSION"), "utf8").trim();
assert.strictEqual(readInstalledVersion(repoRoot), repoVersion);

assert.strictEqual(
  detectRuntime({
    PLUGIN_ROOT: "/codex/plugin",
    CLAUDE_PLUGIN_ROOT: "/compat/plugin",
  }),
  "codex",
  "Codex-specific variables must win over compatibility aliases"
);
assert.strictEqual(detectRuntime({ PLUGIN_DATA: "/codex/data" }), "codex");
assert.strictEqual(
  detectRuntime({ CLAUDE_PLUGIN_ROOT: "/claude/plugin" }),
  "claude"
);
assert.strictEqual(
  detectRuntime({ CLAUDE_PLUGIN_DATA: "/claude/data" }),
  "claude"
);
assert.strictEqual(detectRuntime({}), "unknown");

const codex = updateInstruction("codex");
assert.match(codex, /codex plugin marketplace upgrade paper-wiki/);
assert.match(codex, /codex plugin add paper-wiki@paper-wiki/);
assert.match(codex, /start a new task/);
assert.doesNotMatch(codex, /\/plugin marketplace update/);

const claude = updateInstruction("claude");
assert.match(claude, /\/plugin marketplace update paper-wiki/);
assert.doesNotMatch(claude, /codex plugin marketplace upgrade/);

const fallback = updateInstruction("unknown");
assert.match(fallback, /\/plugin marketplace update paper-wiki/);
assert.match(fallback, /codex plugin marketplace upgrade paper-wiki/);
assert.match(fallback, /codex plugin add paper-wiki@paper-wiki/);

const now = Date.parse("2026-07-11T00:00:00.000Z");
const freshUpdate = {
  update_available: true,
  installed: "1.2.3",
  latest: "1.2.4",
  checked_at: new Date(now - 1000).toISOString(),
};
assert.strictEqual(isFreshCacheForVersion(freshUpdate, "1.2.3", now), true);
assert.strictEqual(shouldDisplayUpdate(freshUpdate, "1.2.3", now), true);
assert.strictEqual(
  isFreshCacheForVersion(freshUpdate, "1.2.2", now),
  false,
  "a different installed version must never reuse this cache"
);
assert.strictEqual(
  shouldDisplayUpdate(freshUpdate, "1.2.2", now),
  false,
  "the main hook must not display another installation's reminder"
);
assert.strictEqual(
  isFreshCacheForVersion(
    { ...freshUpdate, checked_at: new Date(now - ONE_DAY).toISOString() },
    "1.2.3",
    now
  ),
  false,
  "a 24-hour-old cache must be refreshed"
);
assert.strictEqual(
  isFreshCacheForVersion(
    { ...freshUpdate, checked_at: new Date(now + 1000).toISOString() },
    "1.2.3",
    now
  ),
  false,
  "a future-dated cache must not suppress checks indefinitely"
);

const home = path.join(path.parse(process.cwd()).root, "cache-fixture-home");
const cache123 = cachePathForVersion("1.2.3", home);
const cache122 = cachePathForVersion("1.2.2", home);
assert.notStrictEqual(cache123, cache122);
assert.strictEqual(path.dirname(cache123), path.join(home, ".cache", "paper-wiki"));
assert.match(path.basename(cache123), /^update-check-[0-9a-f]{64}\.json$/);
assert.strictEqual(isNewer("v1.2.4", "1.2.3"), true);
assert.strictEqual(isNewer("1.2.3", "1.2.3"), false);

for (const readmeName of ["README.md", "README.en.md"]) {
  const readme = fs.readFileSync(path.join(repoRoot, readmeName), "utf8");
  assert.match(readme, /update-check-<version-hash>\.json/);
  assert.match(readme, /HTTP/);
  assert.match(readme, /24/);
}

console.log("check-update hook runtime/cache tests passed");
