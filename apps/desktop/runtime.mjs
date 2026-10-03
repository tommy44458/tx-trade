import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { chmodSync, copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "dotenv";

export const OPTIONAL_SECRET_NAMES = new Set([
  "OPENAI_API_KEY", "OPENAI_MODEL", "BINGX_API_KEY", "BINGX_API_SECRET", "TYPESAFE_API_KEY",
  "JBLANKED_API_KEY",
]);

// Keep general development configuration, but never inherit legacy PostgreSQL
// connections or override the desktop's private data directory and SQLite file.
// Desktop authentication and optional keys are managed through secure settings.
export function developmentConfig(repoDir) {
  const file = join(repoDir, ".env");
  if (!existsSync(file)) return {};
  return Object.fromEntries(Object.entries(parse(readFileSync(file))).filter(
    ([key]) => !OPTIONAL_SECRET_NAMES.has(key) && !key.startsWith("VITE_")
      && !["APP_DESKTOP", "APP_DESKTOP_TOKEN", "APP_API_PORT", "APP_DATA_DIR", "APP_DB_PATH",
        "DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_DB_SCHEMA"].includes(key),
  ));
}

export function backendEnvironment({ inherited = process.env, config = {}, dataDir, port, token,
  pythonPath }) {
  const excluded = new Set([...OPTIONAL_SECRET_NAMES, "DATABASE_URL", "MIGRATION_DATABASE_URL", "APP_DB_SCHEMA",
    "APP_DB_PATH", "APP_DATA_DIR"]);
  const safeInherited = Object.fromEntries(Object.entries(inherited).filter(
    ([key]) => !excluded.has(key) && !key.startsWith("VITE_"),
  ));
  const safeConfig = Object.fromEntries(Object.entries(config).filter(
    ([key]) => !excluded.has(key) && !key.startsWith("VITE_"),
  ));
  return { ...safeInherited, ...safeConfig,
    APP_MODE: "local", APP_DESKTOP: "1", APP_DESKTOP_TOKEN: token,
    APP_API_PORT: String(port), APP_DATA_DIR: dataDir,
    APP_DB_PATH: join(dataDir, "trade_helper.sqlite3"), PYTHONUNBUFFERED: "1",
    ...(pythonPath ? { PYTHONPATH: pythonPath } : {}),
  };
}

export async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

export function backendCommand({ packaged, resourcesDir, repoDir, pythonPath }) {
  if (packaged) {
    const binary = join(resourcesDir, "backend", process.platform === "win32"
      ? "trade-helper-backend.exe" : "trade-helper-backend");
    if (!existsSync(binary)) throw new Error("桌面版缺少 Python 執行檔，請重新執行 desktop:build。");
    return { command: binary, args: [], cwd: resourcesDir };
  }
  const python = pythonPath || join(repoDir, "apps", "api", ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  if (!existsSync(python)) throw new Error("尚未安裝後端依賴，請在 apps/api 執行 uv sync。");
  return { command: python, args: ["-m", "trade_helper.desktop_runtime"],
    cwd: join(repoDir, "apps", "api") };
}

export function spawnBackend(spec, { port, webDir, env }) {
  return spawn(spec.command, [...spec.args, "--port", String(port), "--web-dir", webDir], {
    cwd: spec.cwd, env, stdio: ["ignore", "pipe", "pipe"],
    detached: process.platform !== "win32", windowsHide: true,
  });
}

export async function waitForBackend(child, origin, token, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error("本地後端未能啟動。請檢查資料目錄的存取權限與後端記錄。");
    }
    try {
      const response = await fetch(`${origin}/api/v1/health`, {
        headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(1500),
      });
      if (response.ok && (await response.json()).status === "ok") return;
    } catch { /* The child may still be applying schema changes. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error("本地後端啟動逾時。請檢查資料目錄與後端記錄。");
}

export async function stopBackend(child) {
  if (!child?.pid) return;
  if (child.exitCode === null && child.signalCode === null) {
    try { child.kill("SIGTERM"); } catch { /* Still try to clean its process group. */ }
    await Promise.race([
      new Promise(resolve => child.once("exit", resolve)),
      new Promise(resolve => setTimeout(resolve, 3500)),
    ]);
  }
  // Stop the entire private group, including workers or a pending Codex turn.
  if (process.platform !== "win32") {
    try { process.kill(-child.pid, "SIGKILL"); } catch { /* Already stopped. */ }
  } else {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
  }
}

export function externalUrl(raw, { developmentOrigin } = {}) {
  const parsed = new URL(raw);
  // An unpackaged build may open its explicitly configured local cloud Worker.
  if (developmentOrigin && !parsed.username && !parsed.password
    && parsed.origin === new URL(developmentOrigin).origin
    && /^(localhost|127\.0\.0\.1)$/.test(parsed.hostname)) return parsed.href;
  if (parsed.protocol !== "https:" || parsed.username || parsed.password
    || /^(localhost|127\.|\[?::1\]?)/i.test(parsed.hostname)) {
    throw new Error("只能開啟外部 HTTPS 網址。");
  }
  return parsed.href;
}

/**
 * Copy the database, with its WAL and shared-memory files, before an update while the
 * local service is not running (it failed to start, so it cannot make its own backup).
 * SQLite applies the WAL when the copy is opened, so recent writes are kept.
 */
export function backupDatabaseFiles(dataDir, version, { fs = { existsSync, mkdirSync, copyFileSync, chmodSync }, now = Date.now } = {}) {
  const source = join(dataDir, "trade_helper.sqlite3");
  if (!fs.existsSync(source)) throw new Error("UPDATE_BACKUP_FAILED");
  const backups = join(dataDir, "backups");
  fs.mkdirSync(backups, { recursive: true, mode: 0o700 });
  const stamp = new Date(now()).toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const target = join(backups, `trade_helper-before-update-${version}-${stamp}-offline.sqlite3`);
  for (const suffix of ["", "-wal", "-shm"]) {
    if (!fs.existsSync(source + suffix)) continue;
    fs.copyFileSync(source + suffix, target + suffix);
    fs.chmodSync(target + suffix, 0o600);
  }
  return target;
}

