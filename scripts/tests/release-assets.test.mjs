import assert from "node:assert/strict";
import test from "node:test";
import { assertReleaseEnvironment, assertReleaseTag, validateUpdateMetadata } from "../release-assets.mjs";

const state = { version: "1.2.3", channel: "stable" };
const tagEnvironment = { GITHUB_REF_TYPE: "tag", GITHUB_REF_NAME: "v1.2.3",
  GITHUB_REF: "refs/tags/v1.2.3", GITHUB_REPOSITORY: "tommy44458/tx-trade" };
const credentials = { CSC_LINK: "test-certificate", CSC_KEY_PASSWORD: "test-password", APPLE_ID: "test@example.com",
  APPLE_APP_SPECIFIC_PASSWORD: "test-password", APPLE_TEAM_ID: "TESTTEAMID" };

test("official release requires an exact tag, prepared product version, and matching repository", () => {
  assert.equal(assertReleaseTag(state, tagEnvironment), "v1.2.3");
  for (const changed of [
    { GITHUB_REF_NAME: "v1.2.4" }, { GITHUB_REF_TYPE: "branch" },
    { GITHUB_REF: "refs/heads/v1.2.3" }, { GITHUB_REPOSITORY: "example/fork" },
  ]) assert.throws(() => assertReleaseTag(state, { ...tagEnvironment, ...changed }));
});

test("official release fails closed for missing secrets, ad-hoc signing, or non-tag CI", () => {
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
