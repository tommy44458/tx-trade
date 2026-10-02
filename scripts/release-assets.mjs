import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import { existsSync, lstatSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { checkReleaseState, parseChangelog } from "./release.mjs";

const defaultRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const signingVariables = ["CSC_LINK", "CSC_KEY_PASSWORD", "APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID"];
export const officialRepository = "tommy44458/txin-trade";

export function assertReleaseTag(state, environment = process.env) {
  const expected = `v${state.version}`;
  if (environment.GITHUB_REF_TYPE !== "tag" || environment.GITHUB_REF_NAME !== expected ||
      environment.GITHUB_REF !== `refs/tags/${expected}`) {
    throw new Error(`An official release requires the exact version tag ${expected}.`);
  }
  if (environment.GITHUB_REPOSITORY !== officialRepository) {
    throw new Error(`Official release metadata is configured for ${officialRepository}; update the release and updater configuration before publishing a fork.`);
  }
  return expected;
}

export function assertReleaseEnvironment(state, environment = process.env) {
  const missing = signingVariables.filter(name => !environment[name]?.trim());
  if (missing.length) {
    throw new Error(`Official release signing/notarization credentials are missing: ${missing.join(", ")}. Use test-release for an explicitly unsigned test build.`);
  }
  if (environment.CSC_IDENTITY_AUTO_DISCOVERY === "false" || environment.CSC_NAME === "-") {
    throw new Error("Official releases cannot disable Developer ID signing or use an ad-hoc identity.");
  }
  if (environment.GITHUB_ACTIONS === "true") assertReleaseTag(state, environment);
}

export function releaseNotes(root = defaultRoot) {
  const state = checkReleaseState(root, { requirePrepared: true });
  const notes = ["CHANGELOG.md", "CHANGELOG.en.md"].map(path => {
    const release = parseChangelog(readFileSync(join(root, path), "utf8"), path).releases
      .find(entry => entry.version === state.version);
    if (!release?.content) throw new Error(`${path} needs release notes for ${state.version}.`);
    return release.content;
  });
  return `# txinTrade ${state.version}\n\n## 繁體中文\n\n${notes[0]}\n\n## English\n\n${notes[1]}\n`;
}

export function validateUpdateMetadata(metadata, state, files) {
  if (metadata?.version !== state.version || !Array.isArray(metadata.files) || metadata.files.length !== 2) {
    throw new Error("Update metadata must contain the exact product version and both DMG/ZIP files.");
  }
  const expected = new Set(files.map(file => file.name));
  for (const entry of metadata.files) {
    if (typeof entry.url !== "string" || basename(entry.url) !== entry.url || !expected.delete(entry.url)) {
      throw new Error("Update metadata contains an unexpected, duplicate, or unsafe asset URL.");
    }
    const file = files.find(item => item.name === entry.url);
    if (entry.sha512 !== file.sha512 || entry.size !== file.size) {
      throw new Error(`Update metadata digest/size does not match ${entry.url}.`);
    }
  }
  const zip = files.find(file => file.name.endsWith(".zip"));
  if (expected.size || metadata.path !== zip.name || metadata.sha512 !== zip.sha512) {
    throw new Error("Update metadata must select the matching ZIP for macOS updates.");
  }
}

function command(commandName, args, options = {}) {
  const result = spawnSync(commandName, args, { encoding: "utf8", ...options });
  if (result.error || result.status !== 0) {
    throw new Error(`${commandName} verification failed: ${result.error?.message ?? (result.stderr || result.stdout).trim()}`);
  }
  return `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
}

function verifyArchitecture(path, { universal = false } = {}) {
  const architectures = command("lipo", ["-archs", path]).trim().split(/\s+/);
  if (!architectures.includes("arm64") || (!universal && architectures.length !== 1)) {
    throw new Error(`Expected ${universal ? "arm64-compatible" : "arm64-only"} executable: ${path}.`);
  }
}

function machOFiles(directory) {
  const result = [];
  for (const name of readdirSync(directory)) {
    const path = join(directory, name);
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) continue;
    if (stat.isDirectory()) result.push(...machOFiles(path));
    else if (stat.isFile()) {
      const header = readFileSync(path).subarray(0, 4).toString("hex");
      if (["feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca", "cafebabf", "bfbafeca"].includes(header)) result.push(path);
    }
  }
  return result;
}

function verifySignedBinary(path, teamId) {
  command("codesign", ["--verify", "--strict", path]);
  const description = command("codesign", ["--display", "--verbose=4", path]);
  if (!description.includes("Authority=Developer ID Application:") ||
      !description.includes(`TeamIdentifier=${teamId}\n`) || !/flags=.*\bruntime\b/.test(description)) {
    throw new Error(`Expected a hardened Developer ID signature from the configured Apple team: ${path}.`);
  }
}

function digestFile(path) {
  const bytes = readFileSync(path);
  return { name: basename(path), size: bytes.length,
    sha512: createHash("sha512").update(bytes).digest("base64"),
    sha256: createHash("sha256").update(bytes).digest("hex") };
}

async function smokeTestBackend(root, app, version) {
  const { availablePort, backendEnvironment, spawnBackend, stopBackend, waitForBackend } =
    await import(pathToFileURL(join(root, "apps/desktop/runtime.mjs")).href);
  // Signing credentials and personal API keys must never enter the test backend.
  const inherited = Object.fromEntries(["PATH", "LANG", "TMPDIR"].filter(name => process.env[name])
    .map(name => [name, process.env[name]]));
  const resources = join(app, "Contents/Resources");
  const binary = join(resources, "backend/trade-helper-backend");
  if (command(binary, ["--version"], { env: inherited }).trim() !== `txinTrade ${version}`) {
    throw new Error("Packaged backend version does not match the release.");
  }
  const port = await availablePort();
  const dataDir = mkdtempSync(join(tmpdir(), "ai-trade-release-check-"));
  const token = randomUUID();
  const child = spawnBackend({ command: binary, args: ["--no-workers"], cwd: resources },
    { port, webDir: join(resources, "web"), env: backendEnvironment({ inherited, dataDir, port, token }) });
  let output = "";
  child.once("error", error => { output = `${output}\n${error.message}`.slice(-6000); });
  child.stdout.on("data", chunk => { output = (output + chunk).slice(-6000); });
  child.stderr.on("data", chunk => { output = (output + chunk).slice(-6000); });
  try {
    const origin = `http://127.0.0.1:${port}`;
    await waitForBackend(child, origin, token, 30_000);
    const unauthorized = await fetch(`${origin}/api/v1/health`, { signal: AbortSignal.timeout(3000) });
    if (![401, 403].includes(unauthorized.status)) throw new Error("Packaged backend health endpoint must require authentication.");
    const response = await fetch(`${origin}/api/v1/health`, {
      headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(3000),
    });
    const health = await response.json();
    if (!response.ok || health.status !== "ok" || health.mode !== "local") {
      throw new Error("Packaged backend health response is invalid.");
    }
  } catch (error) {
    throw new Error(`Packaged backend smoke test failed: ${error.message}\n${output}`);
  } finally {
    await stopBackend(child);
    rmSync(dataDir, { recursive: true, force: true });
  }
}

export async function validateReleaseArtifacts(root = defaultRoot, { official = true, environment = process.env } = {}) {
  const state = checkReleaseState(root, { requirePrepared: official });
  if (official) assertReleaseEnvironment(state, environment);
  const directory = join(root, "apps/desktop/release");
  const stem = `txinTrade-${state.version}-mac-arm64`;
  const assets = [`${stem}.dmg`, `${stem}.zip`, `${stem}.dmg.blockmap`, `${stem}.zip.blockmap`,
    `${state.channel === "beta" ? "beta" : "latest"}-mac.yml`];
  for (const asset of assets) {
    const path = join(directory, asset);
    if (!existsSync(path) || !statSync(path).isFile() || statSync(path).size === 0) {
      throw new Error(`Missing or empty release asset: ${asset}.`);
    }
  }
  const installers = assets.slice(0, 2).map(asset => digestFile(join(directory, asset)));
  // Reuse electron-builder's pinned YAML parser instead of adding another runtime dependency.
  const desktopRequire = createRequire(join(root, "apps/desktop/package.json"));
  const builderRequire = createRequire(desktopRequire.resolve("electron-builder/package.json"));
  const appBuilderRequire = createRequire(builderRequire.resolve("app-builder-lib/package.json"));
  const yaml = appBuilderRequire("js-yaml");
  const metadata = yaml.load(readFileSync(join(directory, assets[4]), "utf8"));
  validateUpdateMetadata(metadata, state, installers);
  const app = join(directory, "mac-arm64/txinTrade.app");
  const backend = join(app, "Contents/Resources/backend");
  const executable = command("plutil", ["-extract", "CFBundleExecutable", "raw", "-o", "-",
    join(app, "Contents/Info.plist")]).trim();
  if (!executable || basename(executable) !== executable || /[\\\x00-\x1f]/.test(executable)) {
    throw new Error("Packaged application has an invalid CFBundleExecutable filename.");
  }
  verifyArchitecture(join(app, "Contents/MacOS", executable));
  verifyArchitecture(join(backend, "trade-helper-backend"));
  const policy = JSON.parse(readFileSync(join(app, "Contents/Resources/update-policy.json"), "utf8"));
  if (policy.enabled !== official || policy.signed !== official || policy.channel !== state.channel ||
      policy.platform !== "darwin" || policy.arch !== "arm64") {
    throw new Error("Packaged updater policy does not match the release mode/version channel.");
  }
  if (official) {
    command("codesign", ["--verify", "--deep", "--strict", app]);
    verifySignedBinary(app, environment.APPLE_TEAM_ID);
    const binaries = machOFiles(backend);
    if (!binaries.length) throw new Error("Packaged Python backend contains no Mach-O binaries.");
    for (const binary of binaries) {
      verifyArchitecture(binary, { universal: true });
      verifySignedBinary(binary, environment.APPLE_TEAM_ID);
    }
    command("xcrun", ["stapler", "validate", app]);
    command("spctl", ["--assess", "--type", "execute", "--verbose", app]);
  }
  await smokeTestBackend(root, app, state.version);
  const entries = assets.map(asset => digestFile(join(directory, asset)));
  const checksums = "SHA256SUMS.txt";
  writeFileSync(join(directory, checksums), entries.map(entry => `${entry.sha256}  ${entry.name}\n`).join(""));
  entries.push(digestFile(join(directory, checksums)));
  const info = { version: state.version, channel: state.channel, platform: "darwin", arch: "arm64",
    signed: official, tag: `v${state.version}`, commit: environment.GITHUB_SHA ?? null, assets: entries };
  writeFileSync(join(directory, "release-info.json"), `${JSON.stringify(info, null, 2)}\n`);
  if (official) writeFileSync(join(directory, "release-notes.md"), releaseNotes(root));
  return info;
}

export async function main(args = process.argv.slice(2), root = defaultRoot) {
  const [mode, ...rest] = args;
  if (mode === "check-tag" && rest.length === 0) {
    const state = checkReleaseState(root, { requirePrepared: true });
    console.log(`Validated tag ${assertReleaseTag(state)} (${state.channel}).`);
  } else if (mode === "notes" && rest.length === 1) {
    writeFileSync(resolve(rest[0]), releaseNotes(root));
  } else if (mode === "validate" && (rest.length === 0 || (rest.length === 1 && rest[0] === "--test"))) {
    const info = await validateReleaseArtifacts(root, { official: !rest.includes("--test") });
    console.log(`Validated ${info.assets.length} ${info.signed ? "signed release" : "test"} assets for ${info.version}.`);
  } else throw new Error("Usage: release-assets.mjs check-tag|notes <file>|validate [--test]");
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => { console.error(`Release validation failed: ${error.message}`); process.exitCode = 1; });
}
