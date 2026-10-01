import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { checkReleaseState } from "./release.mjs";
import { assertReleaseEnvironment, validateReleaseArtifacts } from "./release-assets.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const mode = process.argv[2] || "start";
if (!["start", "package", "test-release", "release"].includes(mode)) {
  throw new Error("Usage: desktop.mjs start|package|test-release|release");
}
const officialRelease = mode === "release";
const distributable = officialRelease || mode === "test-release";
const state = checkReleaseState(root, { requirePrepared: officialRelease });
if (distributable && (process.platform !== "darwin" || process.arch !== "arm64")) {
  throw new Error("Release installers currently support macOS arm64 only; build on an Apple Silicon Mac.");
}
if (officialRelease) assertReleaseEnvironment(state, process.env);

async function run(command, args, cwd = root) {
  const child = spawn(command, args, { cwd, stdio: "inherit", env: process.env });
  await new Promise((resolvePromise, reject) => {
    child.once("error", reject);
    child.once("exit", code => code === 0 ? resolvePromise() : reject(new Error(`${command} failed (${code})`)));
  });
}

await run("uv", ["sync", "--frozen"], join(root, "apps/api"));
await run(pnpm, ["install", "--frozen-lockfile"], join(root, "apps/web"));
await run(pnpm, ["build"], join(root, "apps/web"));
await run(pnpm, ["install", "--frozen-lockfile"], join(root, "apps/desktop"));
// Electron's package keeps runtime installation explicit; install.js reuses its cache.
const electronPackage = join(root, "apps/desktop/node_modules/electron");
if (!existsSync(join(electronPackage, "path.txt"))) {
  await run(process.execPath, [join(electronPackage, "install.js")], join(root, "apps/desktop"));
}
if (mode !== "start") {
  const build = join(root, "apps/desktop/build");
  mkdirSync(build, { recursive: true });
  writeFileSync(join(build, "update-policy.json"), `${JSON.stringify({
    enabled: officialRelease, signed: officialRelease, channel: state.channel,
    platform: process.platform, arch: process.arch,
  }, null, 2)}\n`);
  await run("uv", ["run", "--frozen", "--with", "pyinstaller==6.22.3", "python", "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onedir", "--name", "trade-helper-backend",
    "--paths", join(root, "apps/api/src"),
    "--collect-submodules", "trade_helper",
    "--copy-metadata", "tx-trade-api",
    "--collect-data", "trade_helper",
    "--collect-submodules", "uvicorn", "--hidden-import", "sqlite3",
    "--exclude-module", "psycopg", "--exclude-module", "psycopg_binary",
    "--exclude-module", "keyring", "--exclude-module", "keyrings",
    "--distpath", build, "--workpath", join(build, "pyinstaller"),
    "--specpath", build, join(root, "scripts/desktop_backend.py")], join(root, "apps/api"));
  const destination = join(build, "backend");
  if (existsSync(destination)) rmSync(destination, { recursive: true, force: true });
  // Python.framework and shared-library links must remain relative inside the bundle.
  cpSync(join(build, "trade-helper-backend"), destination, { recursive: true, verbatimSymlinks: true });
  if (distributable) {
    const desktop = join(root, "apps/desktop");
    const config = JSON.parse(readFileSync(join(desktop, "electron-builder.release.json"), "utf8"));
    config.publish.channel = state.channel === "beta" ? "beta" : "latest";
    if (!officialRelease) {
      config.forceCodeSigning = false;
      config.mac.identity = "-";
      config.mac.hardenedRuntime = false;
      config.mac.notarize = false;
      config.dmg.sign = false;
    }
    const generated = join(build, "electron-builder.effective.json");
    writeFileSync(generated, `${JSON.stringify(config, null, 2)}\n`);
    // Never let electron-builder publish. CI uploads only validated files to a draft.
    rmSync(join(desktop, "release"), { recursive: true, force: true });
    await run(pnpm, ["exec", "electron-builder", "--config", generated,
      "--mac", "--arm64", "--publish", "never"], desktop);
    await validateReleaseArtifacts(root, { official: officialRelease });
  } else {
    await run(pnpm, ["package"], join(root, "apps/desktop"));
  }
} else {
  await run(pnpm, ["start"], join(root, "apps/desktop"));
}
