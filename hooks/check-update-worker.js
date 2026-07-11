#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");

const ONE_DAY = 24 * 60 * 60 * 1000;

function readInstalledVersion(pluginRoot) {
  const versionFile = path.join(pluginRoot, "VERSION");
  if (!fs.existsSync(versionFile)) return null;
  const installed = fs.readFileSync(versionFile, "utf8").trim();
  return installed || null;
}

function cachePathForVersion(installed, homeDirectory = os.homedir()) {
  const versionKey = crypto
    .createHash("sha256")
    .update(installed, "utf8")
    .digest("hex");
  return path.join(
    homeDirectory,
    ".cache",
    "paper-wiki",
    `update-check-${versionKey}.json`
  );
}

function isFreshCacheForVersion(cache, installed, now = Date.now()) {
  if (!cache || cache.installed !== installed || !cache.checked_at) return false;
  const checkedAt = new Date(cache.checked_at).getTime();
  const age = now - checkedAt;
  return Number.isFinite(checkedAt) && age >= 0 && age < ONE_DAY;
}

function isNewer(latest, current) {
  const latestParts = latest.replace(/^v/i, "").split(".").map(Number);
  const currentParts = current.replace(/^v/i, "").split(".").map(Number);
  for (let index = 0; index < 3; index += 1) {
    const latestPart = latestParts[index] || 0;
    const currentPart = currentParts[index] || 0;
    if (latestPart > currentPart) return true;
    if (latestPart < currentPart) return false;
  }
  return false;
}

function main() {
  try {
    const pluginRoot =
      process.env.PLUGIN_ROOT ||
      process.env.CLAUDE_PLUGIN_ROOT ||
      path.resolve(__dirname, "..");
    const installed = readInstalledVersion(pluginRoot);
    if (!installed) return;

    // Each installed version owns a separate cache. This prevents Claude Code and
    // Codex installations at different versions from suppressing one another.
    const cachePath = cachePathForVersion(installed);
    const cacheDir = path.dirname(cachePath);
    if (fs.existsSync(cachePath)) {
      try {
        const cache = JSON.parse(fs.readFileSync(cachePath, "utf8"));
        if (isFreshCacheForVersion(cache, installed)) return;
      } catch (_) {
        // A corrupt or mismatched cache is never trusted; retry the check.
      }
    }

    const url =
      "https://raw.githubusercontent.com/u7079256/paper-wiki/main/VERSION";
    const req = https.get(url, { timeout: 5000 }, function (res) {
      if (res.statusCode !== 200) {
        res.resume();
        return;
      }

      let body = "";
      res.setEncoding("utf8");
      res.on("data", function (chunk) {
        body += chunk;
      });
      res.on("end", function () {
        try {
          const latest = body.trim();
          if (!latest) return;

          fs.mkdirSync(cacheDir, { recursive: true });
          const cacheData = {
            update_available: isNewer(latest, installed),
            installed: installed,
            latest: latest,
            checked_at: new Date().toISOString(),
          };
          fs.writeFileSync(
            cachePath,
            JSON.stringify(cacheData, null, 2),
            "utf8"
          );
        } catch (_) {
          // Never surface update-check failures to the host runtime.
        }
      });
    });

    req.on("timeout", function () {
      req.destroy();
    });
    req.on("error", function () {
      // A later session may retry.
    });
  } catch (_) {
    // Never crash, never block the host runtime.
  }
}

if (require.main === module) main();

module.exports = {
  ONE_DAY,
  cachePathForVersion,
  isFreshCacheForVersion,
  isNewer,
  readInstalledVersion,
};
