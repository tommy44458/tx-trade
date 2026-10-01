import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import { readSavedTheme, validateTheme } from "../themes.mjs";

test("native startup reads each workspace appearance without modifying SQLite", () => {
  const directory = mkdtempSync(join(tmpdir(), "trade-helper-theme-"));
  const path = join(directory, "trade_helper.sqlite3");
  let db;
  try {
    assert.equal(readSavedTheme(directory), "system");
    assert.equal(existsSync(path), false);
    db = new DatabaseSync(path);
    db.exec("CREATE TABLE app_preferences(user_id TEXT PRIMARY KEY,value_json TEXT)");
    const insert = db.prepare("INSERT INTO app_preferences VALUES(?,?)");
    insert.run("local-demo", '{"ui_theme":"dark","model_provider":"codex"}');
    insert.run("other", '{"ui_theme":"light"}');
    insert.run("legacy", '{"ui_locale":"en-US"}');
    insert.run("invalid", '{"ui_theme":"wrong"}');
    insert.run("malformed", 'not-json');
    const original = db.prepare("SELECT * FROM app_preferences ORDER BY user_id").all();
    assert.equal(readSavedTheme(directory), "dark");
    assert.equal(readSavedTheme(directory, "other"), "light");
    for (const userId of ["legacy", "invalid", "malformed", "missing"]) {
      assert.equal(readSavedTheme(directory, userId), "system");
    }
    assert.deepEqual(db.prepare("SELECT * FROM app_preferences ORDER BY user_id").all(), original);
  } finally {
    db?.close();
    rmSync(directory, { recursive: true, force: true });
  }
});

test("native appearance defaults to system for an old database and validates IPC input", () => {
  const directory = mkdtempSync(join(tmpdir(), "trade-helper-theme-"));
  const db = new DatabaseSync(join(directory, "trade_helper.sqlite3"));
  try {
    assert.equal(readSavedTheme(directory), "system");
    for (const value of ["system", "light", "dark"]) assert.equal(validateTheme(value), value);
    for (const value of [undefined, null, {}, true, "auto", "LIGHT", "__proto__"]) {
      assert.throws(() => validateTheme(value), /Unsupported/);
    }
  } finally {
    db.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
