import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { checkReleaseState } from "./release.mjs";
import { assertReleaseEnvironment, notarizeDiskImage, validateReleaseArtifacts, validateWindowsArtifacts }
  from "./release-assets.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pnpm = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const mode = process.argv[2] || "start";
if (!["start", "package", "test-release", "release"].includes(mode)) {
  throw new Error("Usage: desktop.mjs start|package|test-release|release");
}
const officialRelease = mode === "release";
const distributable = officialRelease || mode === "test-release";
const state = checkReleaseState(root, { requirePrepared: officialRelease });
const mac = process.platform === "darwin" && process.arch === "arm64";
const windows = process.platform === "win32" && process.arch === "x64";
// macOS releases are signed and notarized. Windows releases are unsigned until a
// code signing certificate exists; their updates rest on the published SHA-512.
if (distributable && !mac && !windows) {
  throw new Error("Installers are built on an Apple Silicon Mac or on Windows x64.");
}
if (officialRelease) assertReleaseEnvironment(state, process.env);

async function run(command, args, cwd = root) {
  // Node only starts a Windows .cmd (such as pnpm.cmd) through a shell.
  const child = spawn(command, args, { cwd, stdio: "inherit", env: process.env,
    shell: process.platform === "win32" && command.endsWith(".cmd") });
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
    enabled: officialRelease, signed: officialRelease && mac, channel: state.channel,
    platform: process.platform, arch: process.arch,
  }, null, 2)}\n`);
  await run("uv", ["run", "--frozen", "--with", "pyinstaller==6.22.3", "python", "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onedir", "--name", "trade-helper-backend",
    // UTF-8 files and pipes everywhere; Windows would otherwise use the locale
    // code page (cp950 for Traditional Chinese). Frozen apps ignore PYTHONUTF8.
    "--python-option", "X utf8",
    "--paths", join(root, "apps/api/src"),
    "--collect-submodules", "trade_helper",
    "--copy-metadata", "tx-trade-api",
    "--collect-data", "trade_helper",
    "--collect-submodules", "uvicorn", "--hidden-import", "sqlite3",
    // Windows has no system time zone database; zoneinfo reads tzdata's files.
    ...(windows ? ["--collect-data", "tzdata", "--hidden-import", "tzdata"] : []),
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
    // No Windows code signing certificate yet: never require or attempt signing there.
    if (!officialRelease || windows) {
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
      ...(windows ? ["--win", "--x64"] : ["--mac", "--arm64"]), "--publish", "never"], desktop);
    if (windows) {
      const info = await validateWindowsArtifacts(root, { official: officialRelease });
      console.log(`Validated ${info.assets.length} unsigned Windows ${officialRelease ? "release" : "test"} assets.`);
    } else {
      if (officialRelease) await notarizeDiskImage(root);
      await validateReleaseArtifacts(root, { official: officialRelease });
    }
  } else {
    await run(pnpm, ["package"], join(root, "apps/desktop"));
  }
} else {
  await run(pnpm, ["start"], join(root, "apps/desktop"));
}
