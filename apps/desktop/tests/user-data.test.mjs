import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { LEGACY_PRODUCT_NAME, resolveUserData } from "../user-data.mjs";

function fixture(t) {
  const appData = mkdtempSync(join(tmpdir(), "txtrade-user-data-"));
  t.after(() => rmSync(appData, { recursive: true, force: true }));
  const legacy = join(appData, LEGACY_PRODUCT_NAME);
  const current = join(appData, "txTrade");
  return { appData, legacy, current };
}

function seedLegacy(legacy) {
  mkdirSync(join(legacy, "data"), { recursive: true });
  writeFileSync(join(legacy, "data", "trade_helper.sqlite3"), "existing workspace");
}

test("the pre-rename profile moves once into the txTrade directory", t => {
  const f = fixture(t);
  seedLegacy(f.legacy);
  assert.deepEqual(resolveUserData(f), { path: f.current, migrated: true });
  assert.equal(readFileSync(join(f.current, "data", "trade_helper.sqlite3"), "utf8"), "existing workspace");
  assert.equal(existsSync(f.legacy), false);
  assert.deepEqual(resolveUserData(f), { path: f.current, migrated: false });
});

test("an existing txTrade profile is never replaced by the legacy one", t => {
  const f = fixture(t);
  seedLegacy(f.legacy);
  mkdirSync(f.current);
  assert.deepEqual(resolveUserData(f), { path: f.current, migrated: false });
  assert.equal(existsSync(join(f.legacy, "data", "trade_helper.sqlite3")), true);
});

test("fresh installs use the txTrade directory without creating anything", t => {
  const f = fixture(t);
  assert.deepEqual(resolveUserData(f), { path: f.current, migrated: false });
  assert.equal(existsSync(f.current), false);
});

test("a legacy profile locked by a running old build stays in place and in use", t => {
  const f = fixture(t);
  seedLegacy(f.legacy);
  // Chromium's lock is a symlink whose target need not exist.
  symlinkSync("host-12345", join(f.legacy, "SingletonLock"));
  assert.deepEqual(resolveUserData(f), { path: f.legacy, migrated: false });
  assert.equal(existsSync(f.current), false);
});

test("a failed move keeps using the legacy profile instead of starting empty", () => {
  const fs = {
    existsSync: path => path.endsWith(LEGACY_PRODUCT_NAME),
    lstatSync: () => { throw new Error("no lock"); },
    renameSync: () => { throw new Error("permission denied"); },
  };
  assert.deepEqual(resolveUserData({ appData: "/isolated", current: "/isolated/txTrade", fs }),
    { path: join("/isolated", LEGACY_PRODUCT_NAME), migrated: false });
});
