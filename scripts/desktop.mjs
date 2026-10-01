import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const mode = process.argv[2] || "start";
if (!["start", "package"].includes(mode)) throw new Error("Usage: desktop.mjs start|package");

async function run(command, args, cwd = root) {
  const child = spawn(command, args, { cwd, stdio: "inherit", env: process.env });
  await new Promise((resolvePromise, reject) => {
    child.once("error", reject);
    child.once("exit", code => code === 0 ? resolvePromise() : reject(new Error(`${command} failed (${code})`)));
  });
}

await run("uv", ["sync"], join(root, "apps/api"));
await run(pnpm, ["install"], join(root, "apps/web"));
await run(pnpm, ["build"], join(root, "apps/web"));
await run(pnpm, ["install"], join(root, "apps/desktop"));
// Electron's package keeps runtime installation explicit; install.js reuses its cache.
const electronPackage = join(root, "apps/desktop/node_modules/electron");
if (!existsSync(join(electronPackage, "path.txt"))) {
  await run(process.execPath, [join(electronPackage, "install.js")], join(root, "apps/desktop"));
}
if (mode === "package") {
  const build = join(root, "apps/desktop/build");
  mkdirSync(build, { recursive: true });
  await run("uv", ["run", "--with", "pyinstaller", "python", "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onedir", "--name", "trade-helper-backend",
    "--paths", join(root, "apps/api/src"),
    "--collect-submodules", "trade_helper",
    "--collect-data", "trade_helper",
    "--collect-submodules", "uvicorn", "--hidden-import", "sqlite3",
    "--exclude-module", "psycopg", "--exclude-module", "psycopg_binary",
    "--exclude-module", "keyring", "--exclude-module", "keyrings",
    "--distpath", build, "--workpath", join(build, "pyinstaller"),
    "--specpath", build, join(root, "scripts/desktop_backend.py")], join(root, "apps/api"));
  const { cpSync, rmSync } = await import("node:fs");
  const destination = join(build, "backend");
  if (existsSync(destination)) rmSync(destination, { recursive: true, force: true });
  // Python.framework and shared-library links must remain relative inside the bundle.
  cpSync(join(build, "trade-helper-backend"), destination, { recursive: true, verbatimSymlinks: true });
  await run(pnpm, ["package"], join(root, "apps/desktop"));
} else {
  await run(pnpm, ["start"], join(root, "apps/desktop"));
}
