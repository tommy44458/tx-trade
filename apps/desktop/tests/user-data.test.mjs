import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { LEGACY_PRODUCT_NAMES, resolveUserData } from "../user-data.mjs";

const dead = () => false;

function fixture(t) {
  const appData = mkdtempSync(join(tmpdir(), "txintrade-user-data-"));
  t.after(() => rmSync(appData, { recursive: true, force: true }));
  const current = join(appData, "txinTrade");
  // The earlier products only shipped for macOS; migration is exercised there.
  return { appData, current, legacy: name => join(appData, name), platform: "darwin" };
}

test("Windows never had an earlier profile, so nothing is migrated there", t => {
  const f = fixture(t);
  seed(f.legacy("txTrade"));
  assert.deepEqual(resolveUserData({ ...f, platform: "win32", alive: dead }), { path: f.current, migrated: false });
  assert.equal(read(f.legacy("txTrade")), "existing workspace");
});

function seed(directory, content = "existing workspace") {
  mkdirSync(join(directory, "data", "backups"), { recursive: true });
  writeFileSync(join(directory, "data", "trade_helper.sqlite3"), content);
  mkdirSync(join(directory, "Partitions", "ai-trade-helper"), { recursive: true });
  writeFileSync(join(directory, "Local State"), "{}");
}

const read = directory => readFileSync(join(directory, "data", "trade_helper.sqlite3"), "utf8");

for (const name of LEGACY_PRODUCT_NAMES) {
  test(`data from a "${name}" profile moves once into txinTrade`, t => {
    const f = fixture(t);
    seed(f.legacy(name));
    assert.deepEqual(resolveUserData({ ...f, alive: dead }), { path: f.current, migrated: true, from: f.legacy(name) });
    assert.equal(read(f.current), "existing workspace");
    assert.ok(existsSync(join(f.current, "data", "backups")));
    assert.ok(existsSync(join(f.current, "Partitions", "ai-trade-helper")));
    assert.equal(existsSync(join(f.legacy(name), "data")), false);
    assert.deepEqual(resolveUserData({ ...f, alive: dead }), { path: f.current, migrated: false });
  });
}

test("a profile Electron created before migration does not block moving the data", t => {
  const f = fixture(t);
  seed(f.legacy("txTrade"));
  // Chromium bootstrap files, and a database-less data folder, already exist.
  mkdirSync(join(f.current, "data"), { recursive: true });
  writeFileSync(join(f.current, "Local State"), "{}");
  writeFileSync(join(f.current, "data", "market_catalog.json"), "{}");
  assert.equal(resolveUserData({ ...f, alive: dead }).migrated, true);
  assert.equal(read(f.current), "existing workspace");
  assert.ok(readdirSync(f.current).some(entry => entry.startsWith("data.before-migration-")));
});

test("the newest earlier profile wins and older ones are left untouched", t => {
  const f = fixture(t);
  seed(f.legacy("txTrade"), "current data");
  seed(f.legacy("AI Trade Helper"), "older data");
  assert.equal(resolveUserData({ ...f, alive: dead }).from, f.legacy("txTrade"));
  assert.equal(read(f.current), "current data");
  assert.equal(read(f.legacy("AI Trade Helper")), "older data");
});

test("a txinTrade database is never replaced by legacy data", t => {
  const f = fixture(t);
  seed(f.legacy("txTrade"), "legacy");
  seed(f.current, "already txinTrade");
  assert.deepEqual(resolveUserData({ ...f, alive: dead }), { path: f.current, migrated: false });
  assert.equal(read(f.current), "already txinTrade");
  assert.equal(read(f.legacy("txTrade")), "legacy");
});

test("fresh installs use the txinTrade directory without creating anything", t => {
  const f = fixture(t);
  assert.deepEqual(resolveUserData({ ...f, alive: dead }), { path: f.current, migrated: false });
  assert.equal(existsSync(f.current), false);
});

test("a profile in use by a running old build stays in place and in use", t => {
  const f = fixture(t);
  seed(f.legacy("txTrade"));
  symlinkSync("host-12345", join(f.legacy("txTrade"), "SingletonLock"));
  const alive = pid => pid === 12345;
  assert.deepEqual(resolveUserData({ ...f, alive }), { path: f.legacy("txTrade"), migrated: false });
  assert.equal(existsSync(join(f.current, "data")), false);
});

test("a stale lock left by an exited build does not block migration", t => {
  const f = fixture(t);
  seed(f.legacy("txTrade"));
  symlinkSync("host-16838", join(f.legacy("txTrade"), "SingletonLock"));
  assert.equal(resolveUserData({ ...f, alive: dead }).migrated, true);
  assert.equal(read(f.current), "existing workspace");
});

test("a failed move keeps using the legacy profile instead of starting empty", () => {
  const fs = {
    existsSync: path => path.includes("/txTrade/") || path.endsWith("/txTrade"),
    mkdirSync: () => {},
    readlinkSync: () => { throw new Error("no lock"); },
    renameSync: () => { throw new Error("permission denied"); },
  };
  assert.deepEqual(resolveUserData({ appData: "/isolated", current: "/isolated/txinTrade", fs, alive: dead,
    platform: "darwin" }),
    { path: join("/isolated", "txTrade"), migrated: false });
});
