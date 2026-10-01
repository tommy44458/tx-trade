import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import { NATIVE_STRINGS, readSavedLocale, validateLocale } from "../locales.mjs";

test("startup reads the saved workspace language without modifying its database", () => {
  const directory = mkdtempSync(join(tmpdir(), "trade-helper-locale-"));
  const path = join(directory, "trade_helper.sqlite3");
  try {
    assert.equal(readSavedLocale(directory), "zh-TW");
    assert.equal(existsSync(path), false);
    const db = new DatabaseSync(path);
    db.exec("CREATE TABLE app_preferences(user_id TEXT PRIMARY KEY,value_json TEXT)");
    db.prepare("INSERT INTO app_preferences VALUES(?,?)").run("local-demo", '{"ui_locale":"en-US"}');
    db.prepare("INSERT INTO app_preferences VALUES(?,?)").run("other", '{"ui_locale":"zh-TW"}');
    const original = db.prepare("SELECT * FROM app_preferences ORDER BY user_id").all();
    assert.equal(readSavedLocale(directory), "en-US");
    assert.equal(readSavedLocale(directory, "other"), "zh-TW");
    assert.equal(readSavedLocale(directory, "missing"), "zh-TW");
    assert.deepEqual(db.prepare("SELECT * FROM app_preferences ORDER BY user_id").all(), original);
    db.prepare("UPDATE app_preferences SET value_json=? WHERE user_id=?").run('{"ui_locale":"invalid"}', "local-demo");
    assert.equal(readSavedLocale(directory), "zh-TW");
    db.close();
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("native languages cover the same controls and reject unsupported IPC input", () => {
  assert.deepEqual(Object.keys(NATIVE_STRINGS["en-US"]).sort(), Object.keys(NATIVE_STRINGS["zh-TW"]).sort());
  assert.equal(validateLocale("en-US"), "en-US");
  assert.equal(validateLocale("zh-TW"), "zh-TW");
  for (const value of [undefined, null, {}, "en", "zh-CN", "__proto__"])
    assert.throws(() => validateLocale(value), /Unsupported/);
});
