import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { readReleaseInfo } from "../release-info.mjs";

test("installed release notes match the running version and selected language", () => {
  const resourcesDir = mkdtempSync(join(tmpdir(), "trade-helper-release-"));
  try {
    writeFileSync(join(resourcesDir, "version.json"), JSON.stringify({ version: "0.3.0", channel: "stable" }));
    writeFileSync(join(resourcesDir, "CHANGELOG.md"), "# 版本變更紀錄\n\n## [Unreleased]\n\n- 未來功能\n\n## [0.3.0] - 2026-10-02\n\n### Added\n\n- 新版功能\n\n## [0.2.0] - 2026-10-01\n\n- 舊版功能\n");
    writeFileSync(join(resourcesDir, "CHANGELOG.en.md"), "# Changelog\r\n\r\n## [Unreleased]\r\n\r\n## [0.3.0] - 2026-10-02\r\n\r\n### Added\r\n\r\n- Current release\r\n");
    const options = { packaged: true, resourcesDir, repoDir: "/missing", version: "0.3.0" };
    assert.deepEqual(readReleaseInfo({ ...options, locale: "zh-TW" }), {
      version: "0.3.0", channel: "stable", prepared: true, notes: "### Added\n\n- 新版功能",
    });
    assert.equal(readReleaseInfo({ ...options, locale: "en-US" }).notes, "### Added\n\n- Current release");
  } finally { rmSync(resourcesDir, { recursive: true, force: true }); }
});

test("development baseline uses pending notes without inventing a released version", () => {
  const repoDir = mkdtempSync(join(tmpdir(), "trade-helper-release-dev-"));
  try {
    writeFileSync(join(repoDir, "version.json"), JSON.stringify({ version: "0.4.0-beta.2", channel: "beta" }));
    writeFileSync(join(repoDir, "CHANGELOG.en.md"), "# Changelog\n\n## [Unreleased]\n\n- Pending feature\n");
    assert.deepEqual(readReleaseInfo({ packaged: false, resourcesDir: "/missing", repoDir,
      version: "0.4.0-beta.2", locale: "en-US" }), {
      version: "0.4.0-beta.2", channel: "beta", prepared: false, notes: "- Pending feature",
    });
  } finally { rmSync(repoDir, { recursive: true, force: true }); }
});

test("missing files and mismatched metadata never replace the actual application version", () => {
  const resourcesDir = mkdtempSync(join(tmpdir(), "trade-helper-release-missing-"));
  try {
    const options = { packaged: true, resourcesDir, repoDir: "/missing",
      version: "0.4.0-beta.1", locale: "zh-TW" };
    assert.deepEqual(readReleaseInfo(options), {
      version: "0.4.0-beta.1", channel: "beta", prepared: false, notes: "",
    });
    writeFileSync(join(resourcesDir, "version.json"), '{"version":"99.0.0","channel":"stable"}');
    assert.equal(readReleaseInfo(options).channel, "beta");
    writeFileSync(join(resourcesDir, "version.json"), "invalid");
    assert.equal(readReleaseInfo(options).version, "0.4.0-beta.1");
  } finally { rmSync(resourcesDir, { recursive: true, force: true }); }
});

test("sandboxed preload exposes the version supplied by the Electron main process", () => {
  const source = readFileSync(new URL("../preload.cjs", import.meta.url), "utf8");
  let bridge;
  runInNewContext(source, {
    process: { platform: "darwin", argv: ["electron", "--trade-helper-version=0.4.0-beta.3"] },
    require: name => {
      assert.equal(name, "electron");
      return { contextBridge: { exposeInMainWorld: (key, value) => {
        assert.equal(key, "tradeHelper"); bridge = value;
      } }, ipcRenderer: { invoke: () => Promise.resolve() } };
    },
  });
  assert.equal(bridge.version, "0.4.0-beta.3");
  assert.equal(Object.isFrozen(bridge), true);
});
