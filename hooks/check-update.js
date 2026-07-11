#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const {
  cachePathForVersion,
  isFreshCacheForVersion,
  readInstalledVersion,
} = require("./check-update-worker.js");

function detectRuntime(env) {
  // Codex exposes PLUGIN_ROOT / PLUGIN_DATA and also sets the CLAUDE_PLUGIN_*
  // compatibility aliases, so the Codex-specific variables must win.
  if (env.PLUGIN_ROOT || env.PLUGIN_DATA) return "codex";
  if (env.CLAUDE_PLUGIN_ROOT || env.CLAUDE_PLUGIN_DATA) return "claude";
  return "unknown";
}

function updateInstruction(runtime) {
  if (runtime === "codex") {
    return (
      "run in a terminal: codex plugin marketplace upgrade paper-wiki; " +
      "then codex plugin add paper-wiki@paper-wiki; then start a new task"
    );
  }
  if (runtime === "claude") {
    return "run in Claude Code: /plugin marketplace update paper-wiki";
  }
  return (
    "Claude Code: /plugin marketplace update paper-wiki; " +
    "Codex terminal: codex plugin marketplace upgrade paper-wiki, then " +
    "codex plugin add paper-wiki@paper-wiki and start a new task"
  );
}

function shouldDisplayUpdate(cache, installed, now = Date.now()) {
  return (
    isFreshCacheForVersion(cache, installed, now) &&
    cache.update_available === true
  );
}

function main() {
  try {
    const pluginRoot =
      process.env.PLUGIN_ROOT ||
      process.env.CLAUDE_PLUGIN_ROOT ||
      path.resolve(__dirname, "..");
    const installed = readInstalledVersion(pluginRoot);

    // Spawn the background worker (detached, fire-and-forget)
    const workerPath = path.join(__dirname, "check-update-worker.js");
    const child = spawn(process.execPath, [workerPath], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
      env: Object.assign({}, process.env, { CLAUDE_PLUGIN_ROOT: pluginRoot }),
    });
    child.unref();

    // Only this exact installed version's fresh cache may drive a reminder.
    if (!installed) return;
    const cachePath = cachePathForVersion(installed);

    if (fs.existsSync(cachePath)) {
      const raw = fs.readFileSync(cachePath, "utf8");
      const cache = JSON.parse(raw);

      if (shouldDisplayUpdate(cache, installed)) {
        process.stderr.write(
          "\x1b[33m⬆ paper-wiki update available (installed: " +
            cache.installed +
            ", latest: " +
            cache.latest +
            ") — " +
            updateInstruction(detectRuntime(process.env)) +
            "\x1b[0m\n"
        );
      }
    }
  } catch (_) {
    // Never crash, never block
  }
}

if (require.main === module) main();

module.exports = { detectRuntime, shouldDisplayUpdate, updateInstruction };
