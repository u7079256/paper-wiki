#!/usr/bin/env node
"use strict";

(function () {
  try {
    const fs = require("fs");
    const path = require("path");
    const os = require("os");
    const https = require("https");

    const pluginRoot =
      process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, "..");

    // Read installed version
    const versionFile = path.join(pluginRoot, "VERSION");
    if (!fs.existsSync(versionFile)) {
      process.exit(0);
    }
    const installed = fs.readFileSync(versionFile, "utf8").trim();
    if (!installed) {
      process.exit(0);
    }

    // Cache location
    const cacheDir = path.join(os.homedir(), ".cache", "paper-wiki");
    const cachePath = path.join(cacheDir, "update-check.json");

    // Skip if cache is fresh (< 24 hours)
    if (fs.existsSync(cachePath)) {
      try {
        const cache = JSON.parse(fs.readFileSync(cachePath, "utf8"));
        if (cache && cache.checked_at) {
          const age = Date.now() - new Date(cache.checked_at).getTime();
          const ONE_DAY = 24 * 60 * 60 * 1000;
          if (age < ONE_DAY) {
            process.exit(0);
          }
        }
      } catch (_) {
        // Cache corrupt — re-check
      }
    }

    // Fetch latest VERSION from GitHub
    const url =
      "https://raw.githubusercontent.com/u7079256/paper-wiki/main/VERSION";

    const req = https.get(url, { timeout: 5000 }, function (res) {
      if (res.statusCode !== 200) {
        res.resume();
        process.exit(0);
      }

      let body = "";
      res.setEncoding("utf8");
      res.on("data", function (chunk) {
        body += chunk;
      });
      res.on("end", function () {
        try {
          const latest = body.trim();
          if (!latest) {
            process.exit(0);
          }

          const updateAvailable = isNewer(latest, installed);

          // Ensure cache directory exists
          fs.mkdirSync(cacheDir, { recursive: true });

          // Write cache
          const cacheData = {
            update_available: updateAvailable,
            installed: installed,
            latest: latest,
            checked_at: new Date().toISOString(),
          };
          fs.writeFileSync(cachePath, JSON.stringify(cacheData, null, 2), "utf8");
        } catch (_) {
          // Silently exit
        }
        process.exit(0);
      });
    });

    req.on("timeout", function () {
      req.destroy();
      process.exit(0);
    });

    req.on("error", function () {
      process.exit(0);
    });

    // Compare semver: returns true if latest > installed
    function isNewer(latest, current) {
      latest = latest.replace(/^v/i, "");
      current = current.replace(/^v/i, "");
      var lp = latest.split(".").map(Number);
      var cp = current.split(".").map(Number);
      for (var i = 0; i < 3; i++) {
        var l = lp[i] || 0;
        var c = cp[i] || 0;
        if (l > c) return true;
        if (l < c) return false;
      }
      return false;
    }
  } catch (_) {
    process.exit(0);
  }
})();
