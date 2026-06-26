#!/usr/bin/env node
"use strict";

try {
  const fs = require("fs");
  const path = require("path");
  const os = require("os");
  const { spawn } = require("child_process");

  const pluginRoot =
    process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, "..");

  // Spawn the background worker (detached, fire-and-forget)
  const workerPath = path.join(__dirname, "check-update-worker.js");
  const child = spawn(process.execPath, [workerPath], {
    detached: true,
    stdio: "ignore",
    windowsHide: true,
    env: Object.assign({}, process.env, { CLAUDE_PLUGIN_ROOT: pluginRoot }),
  });
  child.unref();

  // Read cache and print reminder if an update is available
  const cacheDir = path.join(os.homedir(), ".cache", "paper-wiki");
  const cachePath = path.join(cacheDir, "update-check.json");

  if (fs.existsSync(cachePath)) {
    const raw = fs.readFileSync(cachePath, "utf8");
    const cache = JSON.parse(raw);

    if (cache && cache.checked_at) {
      const age = Date.now() - new Date(cache.checked_at).getTime();
      const ONE_DAY = 24 * 60 * 60 * 1000;

      if (age < ONE_DAY && cache.update_available === true) {
        process.stderr.write(
          "\x1b[33m⬆ paper-wiki update available (installed: " +
            cache.installed +
            ", latest: " +
            cache.latest +
            ") — run: /plugin marketplace update paper-wiki\x1b[0m\n"
        );
      }
    }
  }
} catch (_) {
  // Never crash, never block
}
