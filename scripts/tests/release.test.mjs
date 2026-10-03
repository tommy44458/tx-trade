import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { checkReleaseState, compareVersions, main, parseChangelog, parseVersion,
  prepareRelease, pythonVersion, setProductVersion } from "../release.mjs";

const trackedPaths = ["version.json", "package.json", "apps/desktop/package.json", "apps/web/package.json",
  "apps/api/pyproject.toml", "apps/api/uv.lock", "CHANGELOG.md", "CHANGELOG.en.md", ".env", ".git/HEAD"];
const fixtureFiles = {
  "version.json": '{"version":"0.2.0","channel":"stable"}\n',
  "package.json": '{"name":"product","version":"0.2.0","private":true}\n',
  "apps/desktop/package.json": '{"name":"desktop","version":"0.2.0","build":{"files":["main.mjs"]}}\n',
  "apps/web/package.json": '{"name":"web","version":"0.2.0","scripts":{"build":"vite build"}}\n',
  "apps/api/pyproject.toml": '[project]\nname = "tx-trade-api"\nversion = "0.2.0"\ndependencies = ["fastapi"]\n\n[tool.example]\nvalue = "untouched"\n',
  "apps/api/uv.lock": 'version = 1\n\n[[package]]\nname = "tx-trade-api"\nversion = "0.2.0"\nsource = { editable = "." }\n\n[package.metadata]\nrequires-dist = [{ name = "fastapi" }]\n\n[[package]]\nname = "another-package"\nversion = "0.2.0"\nsource = { registry = "https://example.test" }\n',
  "CHANGELOG.md": '# 更新紀錄\n\n版本尚未發布。\n\n## [Unreleased]\n\n### 新增\n\n- 中文產品版本機制。\n- 第二行中文更新內容。\n',
  "CHANGELOG.en.md": '# Changelog\n\nThis version has not been published.\n\n## [Unreleased]\n\n### Added\n\n- Product version tooling.\n- A second line of English release notes.\n',
};

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "ai-trade-release-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  for (const [path, content] of Object.entries(fixtureFiles)) {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), content);
  }
  mkdirSync(join(root, ".git"));
  writeFileSync(join(root, ".git/HEAD"), "ref: refs/heads/release-test\n");
  writeFileSync(join(root, ".env"), "FAKE_SECRET=untouched-test-value\n");
  return root;
}

function snapshot(root) {
  return Object.fromEntries(trackedPaths.map(path => [path, readFileSync(join(root, path), "utf8")]));
}

function rewrite(root, path, transform) {
  const target = join(root, path);
  writeFileSync(target, transform(readFileSync(target, "utf8")));
}

function updateJson(root, path, values) {
  rewrite(root, path, source => `${JSON.stringify({ ...JSON.parse(source), ...values }, null, 2)}\n`);
}

function replaceUnreleased(root, path, notes) {
  rewrite(root, path, source => source.replace(/(## \[Unreleased\]\n)[\s\S]*?(?=\n## \[|$)/, `$1\n${notes}\n`));
}

test("the initial baseline checks without fabricating a released version or modifying files", t => {
  const root = fixture(t);
  const before = snapshot(root);
  assert.deepEqual(checkReleaseState(root), { version: "0.2.0", channel: "stable", prepared: false, releases: [] });
  assert.deepEqual(snapshot(root), before);
  assert.throws(() => checkReleaseState(root, { requirePrepared: true }), /dated section/);
});

test("strict product versions and channels distinguish stable, beta, and Python package versions", () => {
  for (const version of ["0.0.0", "1.2.3", "1.2.3-beta.0", "1.2.3-beta.12"]) assert.equal(parseVersion(version).version, version);
  for (const version of ["v1.2.3", "01.2.3", "1.02.3", "1.2.03", "1.2.3-beta.01", "1.2.3-alpha.1", "1.2.3+build", "1.2", "1.2.3-beta", null]) {
    assert.throws(() => parseVersion(version), /Invalid product version/);
  }
  assert.equal(pythonVersion("1.2.3-beta.12"), "1.2.3b12");
  assert.equal(pythonVersion("1.2.3"), "1.2.3");
  assert.equal(compareVersions("1.2.3-beta.2", "1.2.3-beta.10"), -1);
  assert.equal(compareVersions("1.2.3", "1.2.3-beta.99"), 1);
  assert.equal(compareVersions("1.3.0-beta.1", "1.2.99"), 1);
  assert.equal(compareVersions("9007199254740993.0.0", "9007199254740992.0.0"), 1);
});

test("check detects drift in each application and the Python lock", async t => {
  for (const path of trackedPaths.slice(1, 6)) {
    await t.test(path, child => {
      const root = fixture(child);
      if (path.endsWith(".json")) updateJson(root, path, { version: "0.1.0" });
      else rewrite(root, path, source => source.replace('version = "0.2.0"', 'version = "0.1.0"'));
      assert.throws(() => checkReleaseState(root), error => error.message.includes("Version drift") && error.message.includes(path));
    });
  }
});

test("synchronizing canonical version repairs drift and preserves unrelated content", t => {
  const root = fixture(t);
  const initial = snapshot(root);
  updateJson(root, "apps/web/package.json", { version: "0.0.0", customReleaseMetadata: "keep me" });
  rewrite(root, "apps/api/uv.lock", source => source.replace('version = "0.2.0"', 'version = "0.1.0"'));
  setProductVersion(root);
  assert.equal(JSON.parse(readFileSync(join(root, "apps/web/package.json"), "utf8")).customReleaseMetadata, "keep me");
  assert.equal(readFileSync(join(root, "apps/desktop/package.json"), "utf8"), initial["apps/desktop/package.json"]);
  assert.equal(readFileSync(join(root, "apps/api/uv.lock"), "utf8"), initial["apps/api/uv.lock"]);
  assert.equal(readFileSync(join(root, ".env"), "utf8"), initial[".env"]);
  assert.equal(readFileSync(join(root, ".git/HEAD"), "utf8"), initial[".git/HEAD"]);
  assert.equal(checkReleaseState(root).version, "0.2.0");
});

test("channel mismatches and malformed versions fail before changing any files", async t => {
  for (const [version, channel] of [["0.2.1-beta.1", "stable"], ["0.2.1", "beta"], ["0.2.1", "nightly"], ["0.2.1-beta.01", "beta"]]) {
    await t.test(`${version}/${channel}`, child => {
      const root = fixture(child);
      const before = snapshot(root);
      assert.throws(() => setProductVersion(root, version, { channel }));
      assert.deepEqual(snapshot(root), before);
    });
  }
});

test("canonical manifest and missing changelog validation are explicit", t => {
  const root = fixture(t);
  updateJson(root, "version.json", { channel: "beta" });
  assert.throws(() => checkReleaseState(root), /does not match channel/);
  updateJson(root, "version.json", { channel: "stable" });
  rmSync(join(root, "CHANGELOG.en.md"));
  assert.throws(() => checkReleaseState(root), /ENOENT/);
});

test("changelog items must each be one short sentence", () => {
  const head = "## [Unreleased]\n\n";
  assert.doesNotThrow(() => parseChangelog(`${head}- Thousands like 8,500 and v1.0.4 stay as one sentence.\n- 中文一句話，可以有逗號。\n`));
  assert.throws(() => parseChangelog(`${head}- First sentence. Second sentence.\n`), /one sentence/);
  assert.throws(() => parseChangelog(`${head}- 第一句。第二句。\n`), /one sentence/);
  assert.throws(() => parseChangelog(`${head}- ${"字".repeat(61)}\n`), /longer than 60/);
  assert.throws(() => parseChangelog(`${head}- ${"word ".repeat(40)}\n`), /longer than 160/);
  assert.throws(() => parseChangelog(`${head}## [0.2.0] - 2026-10-02\n\n- One sentence\n  that wraps. Then another.\n`), /0\.2\.0 note must be one sentence/);
});

test("changelogs reject duplicate sections, missing notes, invalid dates, and unordered releases", () => {
  const unreleased = "# Changes\n\n## [Unreleased]\n\n";
  assert.throws(() => parseChangelog("# No sections\n"), /missing/);
  assert.throws(() => parseChangelog(`${unreleased}## [Unreleased]\n`), /duplicate/);
  assert.throws(() => parseChangelog(`${unreleased}## [0.2.0]\n\n- Note\n`), /valid.*date/);
  assert.throws(() => parseChangelog(`${unreleased}## [0.2.0] - 2026-02-30\n\n- Note\n`), /valid.*date/);
  assert.throws(() => parseChangelog(`${unreleased}## [0.2.0] - 2026-10-02\n\n### Added\n`), /no release notes/);
  assert.throws(() => parseChangelog(`${unreleased}## [0.2.0] - 2026-10-02\n\n- First\n\n## [0.2.0] - 2026-10-01\n\n- Second\n`), /duplicate/);
  assert.throws(() => parseChangelog(`${unreleased}## [0.2.0] - 2026-10-02\n\n- First\n\n## [0.2.1] - 2026-10-01\n\n- Second\n`), /descending/);
});

test("the first release can prepare the existing baseline and retain both languages exactly", t => {
  const root = fixture(t);
  const before = snapshot(root);
  const result = prepareRelease(root, "0.2.0", { date: "2026-10-02" });
  assert.deepEqual(result, { version: "0.2.0", channel: "stable", prepared: true,
    releases: [{ version: "0.2.0", date: "2026-10-02" }] });
  for (const path of ["CHANGELOG.md", "CHANGELOG.en.md"]) {
    const original = parseChangelog(before[path]);
    const nextSource = readFileSync(join(root, path), "utf8");
    const next = parseChangelog(nextSource);
    assert.equal(next.releases[0].content, original.unreleased.content);
    assert.equal(next.preamble, original.preamble);
    assert.equal(next.unreleased.content, "");
    assert.equal(nextSource.includes("\\n"), false);
  }
  assert.equal(readFileSync(join(root, ".env"), "utf8"), before[".env"]);
  assert.equal(readFileSync(join(root, ".git/HEAD"), "utf8"), before[".git/HEAD"]);
});

test("a beta may be synchronized before preparation, then promoted to a stable release", t => {
  const root = fixture(t);
  setProductVersion(root, "0.2.1-beta.2");
  assert.equal(checkReleaseState(root).channel, "beta");
  assert.match(readFileSync(join(root, "apps/api/pyproject.toml"), "utf8"), /^version = "0\.2\.1b2"$/m);
  assert.match(readFileSync(join(root, "apps/api/uv.lock"), "utf8"), /^version = "0\.2\.1b2"$/m);
  prepareRelease(root, "0.2.1-beta.2", { date: "2026-10-02" });
  replaceUnreleased(root, "CHANGELOG.md", "### 修正\n\n- 正式版中文更新內容。\n- 第二行保持實際換行。");
  replaceUnreleased(root, "CHANGELOG.en.md", "### Fixed\n\n- Stable release notes in English.\n- A second line with a real newline.");
  setProductVersion(root, "0.2.1", { channel: "stable" });
  const result = prepareRelease(root, "0.2.1", { date: "2026-10-03" });
  assert.deepEqual(result.releases.map(release => release.version), ["0.2.1", "0.2.1-beta.2"]);
  assert.match(readFileSync(join(root, "CHANGELOG.md"), "utf8"), /中文更新內容。\n- 第二行/);
  assert.match(readFileSync(join(root, "CHANGELOG.en.md"), "utf8"), /English\.\n- A second/);
  assert.match(readFileSync(join(root, "apps/api/pyproject.toml"), "utf8"), /^version = "0\.2\.1"$/m);
});

test("duplicate, decreasing, invalid-date, and empty-note preparation are non-mutating", async t => {
  for (const [version, date, pattern] of [["0.2.0", "2026-10-03", /already/], ["0.1.9", "2026-10-03", /decrease/], ["0.2.1", "2026-02-30", /valid/], ["0.2.1", "2026-10-03", /Unreleased notes/]]) {
    await t.test(version + date, child => {
      const root = fixture(child);
      prepareRelease(root, "0.2.0", { date: "2026-10-02" });
      const before = snapshot(root);
      assert.throws(() => prepareRelease(root, version, { date }), pattern);
      assert.deepEqual(snapshot(root), before);
    });
  }
});

test("bilingual mismatches fail before preparation", async t => {
  for (const mismatch of ["version", "date", "unreleased"]) {
    await t.test(mismatch, child => {
      const root = fixture(child);
      prepareRelease(root, "0.2.0", { date: "2026-10-02" });
      if (mismatch === "version") rewrite(root, "CHANGELOG.en.md", source => source.replace("[0.2.0]", "[0.2.1]"));
      if (mismatch === "date") rewrite(root, "CHANGELOG.en.md", source => source.replace("2026-10-02", "2026-10-03"));
      if (mismatch === "unreleased") replaceUnreleased(root, "CHANGELOG.en.md", "- English only");
      const before = snapshot(root);
      assert.throws(() => prepareRelease(root, "0.2.1"), /both.*languages|Chinese and English/);
      assert.deepEqual(snapshot(root), before);
    });
  }
});

test("product and prepared changelog versions cannot silently disagree", t => {
  const root = fixture(t);
  prepareRelease(root, "0.2.0", { date: "2026-10-02" });
  updateJson(root, "version.json", { version: "0.1.9" });
  assert.throws(() => setProductVersion(root), /ahead/);
  updateJson(root, "version.json", { version: "0.2.1" });
  assert.throws(() => setProductVersion(root), /matching release notes/);
});

test("malformed Python project or lock metadata fail before synchronization", async t => {
  for (const path of ["apps/api/pyproject.toml", "apps/api/uv.lock"]) {
    await t.test(path, child => {
      const root = fixture(child);
      rewrite(root, path, source => source.replace('version = "0.2.0"', 'removed_version = "0.2.0"'));
      const before = snapshot(root);
      assert.throws(() => setProductVersion(root, "0.2.1"), /expected one/);
      assert.deepEqual(snapshot(root), before);
    });
  }
});

test("CLI rejects unknown and incomplete arguments without mutating files", t => {
  const root = fixture(t);
  const before = snapshot(root);
  for (const args of [["publish"], ["check", "0.2.0"], ["version", "--date", "2026-10-02"],
    ["version", "--channel"], ["version", "0.2.1", "--channel", "stable", "--channel", "beta"],
    ["prepare"], ["prepare", "0.2.1", "--unknown"], ["check", "--require-release", "--require-release"]]) {
    assert.throws(() => main(args, root));
  }
  assert.deepEqual(snapshot(root), before);
});

test("CLI help works without reading a repository and never changes release files", t => {
  const root = fixture(t);
  const before = snapshot(root);
  const output = [];
  t.mock.method(console, "log", value => output.push(value));
  assert.deepEqual(main(["--help"], join(root, "missing")), { help: true });
  assert.deepEqual(main(["prepare", "--help"], root), { help: true });
  assert.match(output[0], /no commit, tag, push, or publish/);
  assert.match(output[0], /release:check \[--require-release\]/);
  assert.deepEqual(snapshot(root), before);
});
