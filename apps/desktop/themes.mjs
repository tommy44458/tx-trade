import { existsSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

export function validateTheme(theme) {
  if (theme !== "system" && theme !== "light" && theme !== "dark") {
    throw new Error("Unsupported appearance");
  }
  return theme;
}

// Only the appearance preference is read before the backend starts. No schema,
// credential or authentication access is needed to color the native window.
export function readSavedTheme(dataDirectory, userId = "local-demo") {
  const path = join(dataDirectory, "trade_helper.sqlite3");
  if (!existsSync(path)) return "system";
  let db;
  try {
    db = new DatabaseSync(path, { readOnly: true });
    const row = db.prepare("SELECT json_extract(value_json,'$.ui_theme') AS theme "
      + "FROM app_preferences WHERE user_id=?").get(userId);
    return validateTheme(row?.theme);
  } catch { return "system"; }
  finally { db?.close(); }
}
