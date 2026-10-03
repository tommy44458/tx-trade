import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const sourceRoot = fileURLToPath(new URL("../", import.meta.url));
// The draft publisher runs on Linux CI; its stand-in gh is a shebang script.
const linuxPublisher = { skip: process.platform === "win32" && "the publisher runs on Linux" };

function fixture() {
  const root = mkdtempSync(join(tmpdir(), "ai-trade-publish-test-"));
  const put = (name, content) => {
    const path = join(root, name);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content);
  };
  for (const name of ["package.json", "apps/desktop/package.json", "apps/web/package.json"]) {
    put(name, JSON.stringify({ version: "1.2.3" }));
  }
  put("version.json", JSON.stringify({ version: "1.2.3", channel: "stable" }));
  put("apps/api/pyproject.toml", '[project]\nname = "tx-trade-api"\nversion = "1.2.3"\n');
  put("apps/api/uv.lock", '[[package]]\nname = "tx-trade-api"\nversion = "1.2.3"\n');
  for (const name of ["CHANGELOG.md", "CHANGELOG.en.md"]) {
    put(name, "## [Unreleased]\n\n## [1.2.3] - 2026-10-02\n\n- Current release notes.\n");
  }
  for (const name of ["release.mjs", "release-assets.mjs", "publish-draft.mjs"]) {
    mkdirSync(join(root, "scripts"), { recursive: true });
    cpSync(join(sourceRoot, name), join(root, "scripts", name));
  }
  const names = [".dmg", ".zip", ".dmg.blockmap", ".zip.blockmap"]
    .map(suffix => `txinTrade-1.2.3-mac-arm64${suffix}`);
  names.push("latest-mac.yml", "SHA256SUMS.txt");
  const assets = names.map(name => {
    const data = `test asset ${name}\n`;
    put(`assets/${name}`, data);
    return { name, size: Buffer.byteLength(data), sha256: createHash("sha256").update(data).digest("hex") };
  });
  const info = { version: "1.2.3", channel: "stable", tag: "v1.2.3", commit: "test-commit",
    signed: true, platform: "darwin", arch: "arm64", assets };
  put("assets/release-info.json", JSON.stringify(info));
  put("assets/release-notes.md", "# txinTrade 1.2.3\n\n## 繁體中文\n\n- Current release notes.\n\n## English\n\n- Current release notes.\n");
  put("bin/gh", '#!/usr/bin/env node\nconst fs = require("node:fs");\n'
    + 'fs.appendFileSync(process.env.TEST_CALLS, JSON.stringify(process.argv.slice(2)) + "\\n");\n'
    + 'process.stdout.write(process.argv[2] === "api" ? process.env.TEST_RELEASES : "{}");\n');
  chmodSync(join(root, "bin/gh"), 0o755);
  return { root, info, put };
}

function publish(f, releases = []) {
  return spawnSync(process.execPath, [join(f.root, "scripts/publish-draft.mjs"), join(f.root, "assets")], {
    cwd: f.root, encoding: "utf8", env: {
      PATH: `${join(f.root, "bin")}${delimiter}${process.env.PATH}`,
      GITHUB_REF_TYPE: "tag", GITHUB_REF_NAME: "v1.2.3", GITHUB_REF: "refs/tags/v1.2.3",
      GITHUB_REPOSITORY: "tommy44458/txin-trade", GITHUB_SHA: "test-commit",
      GH_TOKEN: "test-token", TEST_CALLS: join(f.root, "calls.jsonl"), TEST_RELEASES: JSON.stringify([releases]),
    },
  });
}

function calls(f) {
  return existsSync(join(f.root, "calls.jsonl"))
    ? readFileSync(join(f.root, "calls.jsonl"), "utf8").trim().split("\n").map(JSON.parse) : [];
}

test("draft creation keeps the release unpublished and uploads only verified assets", linuxPublisher, () => {
  const f = fixture();
  try {
    const result = publish(f);
    assert.equal(result.status, 0, result.stderr);
    const commands = calls(f);
    assert.deepEqual(commands[0].slice(0, 3), ["api", "--paginate", "--slurp"]);
    assert.deepEqual(commands[1].slice(0, 3), ["release", "create", "v1.2.3"]);
    assert.ok(commands[1].includes("--draft"));
    assert.ok(commands[1].includes("--verify-tag"));
    assert.ok(commands[1].includes("--notes-file"));
    assert.equal(commands[1].filter(arg => arg.startsWith(join(f.root, "assets"))).length, 7);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test("reruns replace only an existing draft, while published releases are never changed", linuxPublisher, () => {
  for (const draft of [true, false]) {
    const f = fixture();
    try {
      const result = publish(f, [{ tag_name: "v1.2.3", draft }]);
      const commands = calls(f);
      if (draft) {
        assert.equal(result.status, 0, result.stderr);
        assert.deepEqual(commands[1].slice(0, 3), ["release", "edit", "v1.2.3"]);
        assert.ok(commands[1].includes("--draft"));
        assert.deepEqual(commands[2].slice(0, 3), ["release", "upload", "v1.2.3"]);
        assert.ok(commands[2].includes("--clobber"));
      } else {
        assert.notEqual(result.status, 0);
        assert.match(result.stderr, /published assets will not be overwritten/);
        assert.equal(commands.length, 1);
      }
    } finally { rmSync(f.root, { recursive: true, force: true }); }
  }
});

function windowsAssets(f, overrides = {}) {
  const names = ["txinTrade-1.2.3-win-x64.exe", "txinTrade-1.2.3-win-x64.exe.blockmap", "latest.yml",
    "SHA256SUMS-win.txt"];
  const assets = names.map(name => {
    const data = `windows asset ${name}\n`;
    f.put(`windows/${name}`, data);
    return { name, size: Buffer.byteLength(data), sha256: createHash("sha256").update(data).digest("hex") };
  });
  const info = { version: "1.2.3", channel: "stable", tag: "v1.2.3", commit: "test-commit",
    signed: false, platform: "win32", arch: "x64", assets, ...overrides };
  f.put("windows/release-info.json", JSON.stringify(info));
  return info;
}

function publishBoth(f, releases = []) {
  return spawnSync(process.execPath, [join(f.root, "scripts/publish-draft.mjs"), join(f.root, "assets"),
    join(f.root, "windows")], {
    cwd: f.root, encoding: "utf8", env: {
      PATH: `${join(f.root, "bin")}${delimiter}${process.env.PATH}`,
      GITHUB_REF_TYPE: "tag", GITHUB_REF_NAME: "v1.2.3", GITHUB_REF: "refs/tags/v1.2.3",
      GITHUB_REPOSITORY: "tommy44458/txin-trade", GITHUB_SHA: "test-commit",
      GH_TOKEN: "test-token", TEST_CALLS: join(f.root, "calls.jsonl"), TEST_RELEASES: JSON.stringify([releases]),
    },
  });
}

test("one draft carries the signed macOS and the unsigned Windows assets", linuxPublisher, () => {
  const f = fixture();
  try {
    windowsAssets(f);
    const result = publishBoth(f);
    assert.equal(result.status, 0, result.stderr);
    const create = calls(f)[1];
    assert.deepEqual(create.slice(0, 3), ["release", "create", "v1.2.3"]);
    assert.equal(create.filter(arg => arg.startsWith(join(f.root, "assets"))).length, 7);
    assert.deepEqual(create.filter(arg => arg.startsWith(join(f.root, "windows"))).map(arg => arg.split(/[\\/]/).at(-1)),
      ["txinTrade-1.2.3-win-x64.exe", "txinTrade-1.2.3-win-x64.exe.blockmap", "latest.yml", "SHA256SUMS-win.txt"]);
  } finally { rmSync(f.root, { recursive: true, force: true }); }
});

test("tampered, mislabelled or incomplete Windows assets fail before contacting GitHub", linuxPublisher, () => {
  for (const tamper of ["asset", "signed", "platform", "missing"]) {
    const f = fixture();
    try {
      const info = windowsAssets(f, tamper === "signed" ? { signed: true }
        : tamper === "platform" ? { platform: "darwin" } : {});
      if (tamper === "asset") f.put(`windows/${info.assets[0].name}`, "tampered bytes");
      if (tamper === "missing") f.put("windows/release-info.json", JSON.stringify({ ...info, assets: info.assets.slice(1) }));
      const result = publishBoth(f);
      assert.notEqual(result.status, 0, tamper);
      assert.deepEqual(calls(f), [], tamper);
    } finally { rmSync(f.root, { recursive: true, force: true }); }
  }
});

test("tampered assets/notes or a different commit fail before contacting GitHub", linuxPublisher, () => {
  for (const tamper of ["asset", "notes", "commit"]) {
    const f = fixture();
    try {
      if (tamper === "asset") f.put(`assets/${f.info.assets[0].name}`, "tampered bytes");
      else if (tamper === "notes") f.put("assets/release-notes.md", "Another version's notes");
      else f.put("assets/release-info.json", JSON.stringify({ ...f.info, commit: "another-commit" }));
      const result = publish(f);
      assert.notEqual(result.status, 0);
      assert.deepEqual(calls(f), []);
    } finally { rmSync(f.root, { recursive: true, force: true }); }
  }
});
