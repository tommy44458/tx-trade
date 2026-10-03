import assert from "node:assert/strict";
import test from "node:test";
import { assertReleaseEnvironment, assertReleaseEnvironment as assertMacEnvironment, assertReleaseTag, validateUpdateMetadata, validateWindowsUpdateMetadata,
  windowsAssetNames, withInstallerDigest } from "../release-assets.mjs";

const state = { version: "1.2.3", channel: "stable" };
const tagEnvironment = { GITHUB_REF_TYPE: "tag", GITHUB_REF_NAME: "v1.2.3",
  GITHUB_REF: "refs/tags/v1.2.3", GITHUB_REPOSITORY: "tommy44458/txin-trade" };
const credentials = { CSC_LINK: "test-certificate", CSC_KEY_PASSWORD: "test-password", APPLE_ID: "test@example.com",
  APPLE_APP_SPECIFIC_PASSWORD: "test-password", APPLE_TEAM_ID: "TESTTEAMID" };

test("official release requires an exact tag, prepared product version, and matching repository", () => {
  assert.equal(assertReleaseTag(state, tagEnvironment), "v1.2.3");
  for (const changed of [
    { GITHUB_REF_NAME: "v1.2.4" }, { GITHUB_REF_TYPE: "branch" },
    { GITHUB_REF: "refs/heads/v1.2.3" }, { GITHUB_REPOSITORY: "example/fork" },
  ]) assert.throws(() => assertReleaseTag(state, { ...tagEnvironment, ...changed }));
});

test("official macOS release fails closed for missing secrets, ad-hoc signing, or non-tag CI", () => {
  const assertReleaseEnvironment = (s, environment) => assertMacEnvironment(s, environment, { platform: "darwin" });
  assert.doesNotThrow(() => assertReleaseEnvironment(state, credentials));
  assert.throws(() => assertReleaseEnvironment(state, {}), /credentials are missing/);
  for (const name of Object.keys(credentials)) {
    assert.throws(() => assertReleaseEnvironment(state, { ...credentials, [name]: " " }), /credentials are missing/);
  }
  assert.throws(() => assertReleaseEnvironment(state, { ...credentials, CSC_NAME: "-" }), /ad-hoc/);
  assert.throws(() => assertReleaseEnvironment(state, { ...credentials, CSC_IDENTITY_AUTO_DISCOVERY: "false" }), /disable/);
  assert.throws(() => assertReleaseEnvironment(state, { ...credentials, GITHUB_ACTIONS: "true" }), /exact version tag/);
  assert.doesNotThrow(() => assertReleaseEnvironment(state, { ...credentials, ...tagEnvironment, GITHUB_ACTIONS: "true" }));
});

const files = [
  { name: "app.zip", sha512: "zip-digest", size: 100 },
  { name: "app.dmg", sha512: "dmg-digest", size: 200 },
];
const metadata = { version: "1.2.3", files: files.map(file => ({ url: file.name, sha512: file.sha512, size: file.size })),
  path: "app.zip", sha512: "zip-digest" };

test("update manifest must hash both exact installers and select the ZIP", () => {
  assert.doesNotThrow(() => validateUpdateMetadata(metadata, state, files));
  for (const changed of [
    { version: "1.2.4" }, { files: metadata.files.slice(0, 1) },
    { files: [metadata.files[0], metadata.files[0]] },
    { files: [{ ...metadata.files[0], url: "../app.zip" }, metadata.files[1]] },
    { files: [{ ...metadata.files[0], sha512: "tampered" }, metadata.files[1]] },
    { files: [{ ...metadata.files[0], size: 99 }, metadata.files[1]] },
    { path: "app.dmg" }, { sha512: "tampered" },
  ]) assert.throws(() => validateUpdateMetadata({ ...metadata, ...changed }, state, files));
});

test("stapling the DMG updates only its digest, and the update path keeps the ZIP", () => {
  const metadata = { version: "1.2.3", path: "app.zip", sha512: "zip-digest", releaseDate: "2026-10-02T00:00:00.000Z",
    files: [{ url: "app.zip", sha512: "zip-digest", size: 10 }, { url: "app.dmg", sha512: "old", size: 20 }] };
  const updated = withInstallerDigest(metadata, "app.dmg", { sha512: "stapled", size: 21 });
  assert.deepEqual(updated.files, [{ url: "app.zip", sha512: "zip-digest", size: 10 }, { url: "app.dmg", sha512: "stapled", size: 21 }]);
  assert.equal(updated.path, "app.zip");
  assert.equal(updated.sha512, "zip-digest");
  assert.equal(metadata.files[1].sha512, "old");
  assert.throws(() => withInstallerDigest(metadata, "other.dmg", { sha512: "x", size: 1 }), /no entry/);
});

test("Windows releases are tag-bound but need no signing credentials", () => {
  assert.doesNotThrow(() => assertReleaseEnvironment(state, { ...tagEnvironment, GITHUB_ACTIONS: "true" },
    { platform: "win32" }));
  assert.throws(() => assertReleaseEnvironment(state, { ...tagEnvironment, GITHUB_ACTIONS: "true",
    GITHUB_REF_NAME: "v9.9.9", GITHUB_REF: "refs/tags/v9.9.9" }, { platform: "win32" }), /exact version tag/);
  assert.throws(() => assertReleaseEnvironment(state, tagEnvironment, { platform: "darwin" }), /credentials are missing/);
});

test("Windows update metadata must point at the one installer with its exact digest", () => {
  const names = windowsAssetNames(state);
  assert.deepEqual(names, { installer: "txinTrade-1.2.3-win-x64.exe", blockmap: "txinTrade-1.2.3-win-x64.exe.blockmap",
    metadata: "latest.yml", checksums: "SHA256SUMS-win.txt" });
  assert.equal(windowsAssetNames({ version: "1.3.0-beta.1", channel: "beta" }).metadata, "beta.yml");
  const installer = { name: names.installer, sha512: "digest", size: 10 };
  const good = { version: "1.2.3", path: names.installer, sha512: "digest",
    files: [{ url: names.installer, sha512: "digest", size: 10 }] };
  assert.doesNotThrow(() => validateWindowsUpdateMetadata(good, state, installer));
  for (const bad of [
    { ...good, version: "1.2.2" },
    { ...good, files: [...good.files, { url: "extra.exe", sha512: "digest", size: 10 }] },
    { ...good, files: [{ ...good.files[0], url: "../evil.exe" }] },
    { ...good, files: [{ ...good.files[0], sha512: "other" }] },
    { ...good, files: [{ ...good.files[0], size: 11 }] },
    { ...good, sha512: "other" },
    { ...good, path: "other.exe" },
  ]) {
    assert.throws(() => validateWindowsUpdateMetadata(bad, state, installer), JSON.stringify(bad));
  }
});
