import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { assertReleaseTag, officialRepository, releaseNotes, windowsAssetNames } from "./release-assets.mjs";
import { checkReleaseState } from "./release.mjs";

// Usage: publish-draft.mjs <macOS assets> [<Windows assets>]. Both platforms
// share one draft; Windows reads latest.yml, macOS latest-mac.yml.
const directory = resolve(process.argv[2] ?? "release-assets");
const windowsDirectory = process.argv[3] ? resolve(process.argv[3]) : null;
const state = checkReleaseState(undefined, { requirePrepared: true });
const tag = assertReleaseTag(state);

function verifiedAssets(folder, { platform, arch, signed, names }) {
  const info = JSON.parse(readFileSync(join(folder, "release-info.json"), "utf8"));
  if (info.version !== state.version || info.channel !== state.channel || info.tag !== tag ||
      info.commit !== process.env.GITHUB_SHA || info.signed !== signed || info.platform !== platform ||
      info.arch !== arch || !Array.isArray(info.assets) || info.assets.length !== names.length) {
    throw new Error(`Downloaded ${platform} assets do not match this release, tag, and commit.`);
  }
  const expected = new Set(names);
  const paths = info.assets.map(asset => {
    if (typeof asset.name !== "string" || basename(asset.name) !== asset.name || !expected.delete(asset.name)) {
      throw new Error("Unexpected or duplicate artifact filename.");
    }
    const path = join(folder, asset.name);
    const data = readFileSync(path);
    if (data.length !== asset.size || createHash("sha256").update(data).digest("hex") !== asset.sha256) {
      throw new Error(`Artifact digest/size mismatch: ${asset.name}.`);
    }
    return path;
  });
  if (expected.size) throw new Error(`Required ${platform} release assets are missing.`);
  return paths;
}

const assetPaths = verifiedAssets(directory, { platform: "darwin", arch: "arm64", signed: true, names: [
  `txinTrade-${state.version}-mac-arm64.dmg`,
  `txinTrade-${state.version}-mac-arm64.zip`,
  `txinTrade-${state.version}-mac-arm64.dmg.blockmap`,
  `txinTrade-${state.version}-mac-arm64.zip.blockmap`,
  `${state.channel === "beta" ? "beta" : "latest"}-mac.yml`, "SHA256SUMS.txt",
] });
if (windowsDirectory) {
  const names = windowsAssetNames(state);
  // Unsigned until a Windows code signing certificate exists.
  assetPaths.push(...verifiedAssets(windowsDirectory, { platform: "win32", arch: "x64", signed: false,
    names: [names.installer, names.blockmap, names.metadata, names.checksums] }));
}
const notesPath = join(directory, "release-notes.md");
if (readFileSync(notesPath, "utf8") !== releaseNotes()) {
  throw new Error("Downloaded release notes do not match this version's bilingual changelog.");
}

function gh(args) {
  const result = spawnSync("gh", args, { encoding: "utf8", env: process.env });
  if (result.error || result.status !== 0) throw new Error(result.error?.message ?? result.stderr);
  return result.stdout;
}

const repo = process.env.GITHUB_REPOSITORY;
if (repo !== officialRepository) throw new Error("Unexpected publication repository.");
// Listing with write authentication includes drafts; the tag endpoint may omit them.
const pages = JSON.parse(gh(["api", "--paginate", "--slurp", `repos/${repo}/releases?per_page=100`]));
const matches = pages.flat().filter(release => release.tag_name === tag);
if (matches.length > 1) throw new Error("Multiple releases use this tag; resolve them before retrying.");
const existing = matches[0];
if (existing) {
  if (!existing.draft) throw new Error("This tag already has a published release; published assets will not be overwritten.");
  gh(["release", "edit", tag, "--repo", repo, "--draft", "--title", `txinTrade ${state.version}`,
    "--notes-file", notesPath, ...(state.channel === "beta" ? ["--prerelease"] : ["--prerelease=false"])]);
  gh(["release", "upload", tag, ...assetPaths, "--repo", repo, "--clobber"]);
} else {
  gh(["release", "create", tag, ...assetPaths, "--repo", repo, "--verify-tag", "--draft",
    "--title", `txinTrade ${state.version}`, "--notes-file", notesPath,
    ...(state.channel === "beta" ? ["--prerelease"] : [])]);
}
console.log(`Draft ${tag} is ready for review. It remains unpublished.`);
