import { randomUUID } from "node:crypto";
import { existsSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const defaultRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packagePaths = ["package.json", "apps/desktop/package.json", "apps/web/package.json"];
const changelogPaths = ["CHANGELOG.md", "CHANGELOG.en.md"];
const versionPattern = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-beta\.(0|[1-9]\d*))?$/;

export function parseVersion(version) {
  const match = typeof version === "string" && version.match(versionPattern);
  if (!match) throw new Error(`Invalid product version: ${version}. Use x.y.z or x.y.z-beta.N.`);
  return { version, major: match[1], minor: match[2], patch: match[3], beta: match[4] ?? null };
}

export function compareVersions(left, right) {
  const a = parseVersion(left);
  const b = parseVersion(right);
  for (const field of ["major", "minor", "patch"]) {
    if (BigInt(a[field]) !== BigInt(b[field])) return BigInt(a[field]) > BigInt(b[field]) ? 1 : -1;
  }
  if (a.beta === b.beta) return 0;
  if (a.beta === null) return 1;
  if (b.beta === null) return -1;
  return BigInt(a.beta) > BigInt(b.beta) ? 1 : -1;
}

export function pythonVersion(version) {
  const parsed = parseVersion(version);
  return parsed.beta === null ? version : `${parsed.major}.${parsed.minor}.${parsed.patch}b${parsed.beta}`;
}

function channelFor(version) {
  return parseVersion(version).beta === null ? "stable" : "beta";
}

function validateManifest(manifest) {
  if (!manifest || Array.isArray(manifest) || typeof manifest !== "object") {
    throw new Error("version.json must contain an object with version and channel.");
  }
  parseVersion(manifest.version);
  if (!["stable", "beta"].includes(manifest.channel)) {
    throw new Error("version.json channel must be stable or beta.");
  }
  if (manifest.channel !== channelFor(manifest.version)) {
    throw new Error(`Version ${manifest.version} does not match channel ${manifest.channel}.`);
  }
  return manifest;
}

function validDate(date) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
  const parsed = new Date(`${date}T00:00:00.000Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === date;
}

function hasNotes(content) {
  return content.split(/\r?\n/).some(line => line.trim() && !/^\s*#/.test(line));
}

export function parseChangelog(source, label = "changelog") {
  const headings = [...source.matchAll(/^## (.+)$/gm)];
  if (!headings.length) throw new Error(`${label}: missing ## [Unreleased] section.`);
  const sections = headings.map((heading, index) => {
    const match = heading[1].trim().match(/^\[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?$/);
    if (!match) throw new Error(`${label}: invalid section heading ${heading[0]}.`);
    const end = headings[index + 1]?.index ?? source.length;
    const bodyStart = heading.index + heading[0].length;
    return { version: match[1], date: match[2] ?? null, start: heading.index, end,
      content: source.slice(bodyStart, end).trim() };
  });
  if (sections[0].version !== "Unreleased" || sections[0].date !== null) {
    throw new Error(`${label}: the first section must be ## [Unreleased] without a date.`);
  }
  const seen = new Set();
  for (let index = 0; index < sections.length; index += 1) {
    const section = sections[index];
    if (seen.has(section.version)) throw new Error(`${label}: duplicate section ${section.version}.`);
    seen.add(section.version);
    if (index === 0) continue;
    parseVersion(section.version);
    if (!section.date || !validDate(section.date)) {
      throw new Error(`${label}: ${section.version} needs a valid YYYY-MM-DD preparation date.`);
    }
    if (!hasNotes(section.content)) throw new Error(`${label}: ${section.version} has no release notes.`);
    if (index > 1 && compareVersions(sections[index - 1].version, section.version) <= 0) {
      throw new Error(`${label}: version sections must be in descending order.`);
    }
  }
  return { source, preamble: source.slice(0, sections[0].start), unreleased: sections[0],
    releases: sections.slice(1) };
}

function projectVersionField(source, label) {
  const section = source.match(/^\[project\]\s*\n([\s\S]*?)(?=^\[|$(?![\s\S]))/m);
  if (!section) throw new Error(`${label}: missing [project] section.`);
  const versions = [...section[0].matchAll(/^version\s*=\s*"([^"]+)"\s*$/gm)];
  if (versions.length !== 1) throw new Error(`${label}: expected one project version.`);
  return { version: versions[0][1], start: section.index + versions[0].index,
    length: versions[0][0].length };
}

function lockVersionField(source) {
  const packages = [...source.matchAll(/^\[\[package\]\]\s*\n[\s\S]*?(?=^\[\[package\]\]|$(?![\s\S]))/gm)];
  const matches = packages.filter(entry => /^name\s*=\s*"ai-trade-helper-api"\s*$/m.test(entry[0]));
  if (matches.length !== 1) throw new Error("apps/api/uv.lock: expected one ai-trade-helper-api package.");
  const versions = [...matches[0][0].matchAll(/^version\s*=\s*"([^"]+)"\s*$/gm)];
  if (versions.length !== 1) throw new Error("apps/api/uv.lock: expected one API package version.");
  return { version: versions[0][1], start: matches[0].index + versions[0].index,
    length: versions[0][0].length };
}

function readState(root) {
  const paths = ["version.json", ...packagePaths, "apps/api/pyproject.toml", "apps/api/uv.lock", ...changelogPaths];
  const files = new Map(paths.map(path => [path, readFileSync(join(root, path), "utf8")]));
  const manifest = validateManifest(JSON.parse(files.get("version.json")));
  const packages = packagePaths.map(path => ({ path, data: JSON.parse(files.get(path)) }));
  const project = projectVersionField(files.get("apps/api/pyproject.toml"), "apps/api/pyproject.toml");
  const lock = lockVersionField(files.get("apps/api/uv.lock"));
  const changelogs = changelogPaths.map(path => ({ path, ...parseChangelog(files.get(path), path) }));
  const structure = changelogs.map(changelog => changelog.releases.map(({ version, date }) => ({ version, date })));
  if (JSON.stringify(structure[0]) !== JSON.stringify(structure[1])) {
    throw new Error("Chinese and English changelog versions and preparation dates must match.");
  }
  if (hasNotes(changelogs[0].unreleased.content) !== hasNotes(changelogs[1].unreleased.content)) {
    throw new Error("Unreleased notes must exist in both changelog languages or neither.");
  }
  return { root, files, manifest, packages, project, lock, changelogs };
}

function validateChangelogVersion(state, version, requirePrepared = false) {
  const changelog = state.changelogs[0];
  const newest = changelog.releases[0];
  if (newest && compareVersions(newest.version, version) > 0) {
    throw new Error(`Newest changelog version ${newest.version} is ahead of product version ${version}.`);
  }
  const prepared = newest?.version === version;
  if (!prepared && (requirePrepared || !hasNotes(changelog.unreleased.content))) {
    throw new Error(`Product version ${version} needs matching release notes${requirePrepared ? " in a dated section" : " or Unreleased notes"}.`);
  }
  return prepared;
}

export function checkReleaseState(root = defaultRoot, { requirePrepared = false } = {}) {
  const state = readState(root);
  const expectedPython = pythonVersion(state.manifest.version);
  const drift = state.packages.filter(({ data }) => data.version !== state.manifest.version)
    .map(({ path, data }) => `${path}: ${data.version}`);
  if (state.project.version !== expectedPython) drift.push(`apps/api/pyproject.toml: ${state.project.version}`);
  if (state.lock.version !== expectedPython) drift.push(`apps/api/uv.lock: ${state.lock.version}`);
  if (drift.length) {
    throw new Error(`Version drift from ${state.manifest.version}: ${drift.join(", ")}. Run release:version to synchronize.`);
  }
  const prepared = validateChangelogVersion(state, state.manifest.version, requirePrepared);
  return { version: state.manifest.version, channel: state.manifest.channel, prepared,
    releases: state.changelogs[0].releases.map(({ version, date }) => ({ version, date })) };
}

function replaceField(source, field, version) {
  return `${source.slice(0, field.start)}version = "${version}"${source.slice(field.start + field.length)}`;
}

function versionChanges(state, version, channel) {
  validateManifest({ version, channel });
  const changes = new Map();
  changes.set("version.json", `${JSON.stringify({ ...state.manifest, version, channel }, null, 2)}\n`);
  for (const { path, data } of state.packages) {
    const source = state.files.get(path);
    // Preserve unrelated package formatting when synchronization changes no version.
    changes.set(path, data.version === version ? source : `${JSON.stringify({ ...data, version }, null, 2)}\n`);
  }
  changes.set("apps/api/pyproject.toml", replaceField(state.files.get("apps/api/pyproject.toml"), state.project, pythonVersion(version)));
  changes.set("apps/api/uv.lock", replaceField(state.files.get("apps/api/uv.lock"), state.lock, pythonVersion(version)));
  return changes;
}

function writeChanges(state, changes) {
  const staged = [];
  const committed = [];
  try {
    for (const [path, content] of changes) {
      if (content === state.files.get(path)) continue;
      const target = join(state.root, path);
      if (readFileSync(target, "utf8") !== state.files.get(path)) {
        throw new Error(`${path} changed during release preparation. Retry after reviewing the change.`);
      }
      const temporary = `${target}.${randomUUID()}.tmp`;
      writeFileSync(temporary, content, { mode: statSync(target).mode });
      staged.push({ path, target, temporary });
    }
    for (const item of staged) {
      renameSync(item.temporary, item.target);
      committed.push(item);
    }
  } catch (error) {
    for (const { path, target } of committed.reverse()) writeFileSync(target, state.files.get(path));
    throw error;
  } finally {
    for (const { temporary } of staged) if (existsSync(temporary)) rmSync(temporary);
  }
}

export function setProductVersion(root = defaultRoot, version, { channel } = {}) {
  const state = readState(root);
  const targetVersion = version ?? state.manifest.version;
  const targetChannel = channel ?? (version === undefined ? state.manifest.channel : channelFor(targetVersion));
  validateManifest({ version: targetVersion, channel: targetChannel });
  if (compareVersions(targetVersion, state.manifest.version) < 0) throw new Error("Product version cannot decrease.");
  validateChangelogVersion(state, targetVersion);
  writeChanges(state, versionChanges(state, targetVersion, targetChannel));
  return checkReleaseState(root);
}

export function prepareRelease(root = defaultRoot, version, { channel, date = new Date().toISOString().slice(0, 10) } = {}) {
  const state = readState(root);
  const targetChannel = channel ?? channelFor(version);
  validateManifest({ version, channel: targetChannel });
  if (!validDate(date)) throw new Error("Preparation date must be a valid YYYY-MM-DD date.");
  if (compareVersions(version, state.manifest.version) < 0) throw new Error("Product version cannot decrease.");
  if (state.changelogs[0].releases.some(release => release.version === version)) {
    throw new Error(`Version ${version} already has a changelog section.`);
  }
  const newest = state.changelogs[0].releases[0];
  if (newest && compareVersions(version, newest.version) <= 0) throw new Error("Prepared release must be newer than existing release notes.");
  if (!state.changelogs.every(changelog => hasNotes(changelog.unreleased.content))) {
    throw new Error("Add Unreleased notes in both languages before preparing a release.");
  }
  const changes = versionChanges(state, version, targetChannel);
  for (const changelog of state.changelogs) {
    const older = changelog.source.slice(changelog.unreleased.end).trimStart();
    const prepared = `${changelog.preamble}## [Unreleased]\n\n## [${version}] - ${date}\n\n${changelog.unreleased.content}\n`;
    changes.set(changelog.path, older ? `${prepared}\n${older}` : prepared);
  }
  writeChanges(state, changes);
  return checkReleaseState(root, { requirePrepared: true });
}

function parseArguments(args) {
  const [command = "check", ...rest] = args;
  if (!["check", "version", "prepare"].includes(command)) throw new Error("Usage: release.mjs check|version|prepare [version] [--channel stable|beta] [--date YYYY-MM-DD] [--require-release]");
  const options = {};
  let version;
  for (let index = 0; index < rest.length; index += 1) {
    const argument = rest[index];
    if (argument === "--require-release" && command === "check" && !options.requirePrepared) {
      options.requirePrepared = true;
    } else if (["--channel", "--date"].includes(argument) && command !== "check" &&
      !(argument === "--date" && command !== "prepare")) {
      const key = argument.slice(2);
      if (options[key] !== undefined || !rest[index + 1] || rest[index + 1].startsWith("--")) {
        throw new Error(`Provide ${argument} once with a value.`);
      }
      options[key] = rest[++index];
    } else if (!argument.startsWith("--") && version === undefined && command !== "check") {
      version = argument;
    } else {
      throw new Error(`Unexpected argument: ${argument}`);
    }
  }
  if (command === "prepare" && version === undefined) throw new Error("release:prepare requires a product version.");
  return { command, version, options };
}

export function main(args = process.argv.slice(2), root = defaultRoot) {
  if (args.includes("--help") || args.includes("-h")) {
    console.log("Local product release preparation (no commit, tag, push, or publish):\n"
      + "  pnpm release:check [--require-release]\n"
      + "  pnpm release:version [x.y.z[-beta.N]] [--channel stable|beta]\n"
      + "  pnpm release:prepare <x.y.z[-beta.N]> [--channel stable|beta] [--date YYYY-MM-DD]\n"
      + "\nOmit the version in release:version to synchronize packages with version.json.\n"
      + "release:prepare requires Unreleased notes in both languages and records only a local preparation date.");
    return { help: true };
  }
  const { command, version, options } = parseArguments(args);
  const result = command === "check" ? checkReleaseState(root, options)
    : command === "version" ? setProductVersion(root, version, options)
      : prepareRelease(root, version, options);
  console.log(`Product ${result.version} (${result.channel}): ${result.prepared ? "release notes prepared locally" : "unreleased development baseline"}.`);
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    main();
  } catch (error) {
    console.error(`Release check failed: ${error.message}`);
    process.exitCode = 1;
  }
}
