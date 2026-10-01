import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { assertReleaseTag, officialRepository, releaseNotes } from "./release-assets.mjs";
import { checkReleaseState } from "./release.mjs";

const directory = resolve(process.argv[2] ?? "release-assets");
const info = JSON.parse(readFileSync(join(directory, "release-info.json"), "utf8"));
const state = checkReleaseState(undefined, { requirePrepared: true });
const tag = assertReleaseTag(state);
if (info.version !== state.version || info.channel !== state.channel || info.tag !== tag ||
    info.commit !== process.env.GITHUB_SHA || info.signed !== true || info.platform !== "darwin" ||
    info.arch !== "arm64" || !Array.isArray(info.assets) || info.assets.length !== 6) {
  throw new Error("Downloaded assets do not match this signed release, tag, and commit.");
}
const expected = new Set([
  `txTrade-${state.version}-mac-arm64.dmg`,
  `txTrade-${state.version}-mac-arm64.zip`,
  `txTrade-${state.version}-mac-arm64.dmg.blockmap`,
  `txTrade-${state.version}-mac-arm64.zip.blockmap`,
  `${state.channel === "beta" ? "beta" : "latest"}-mac.yml`, "SHA256SUMS.txt",
]);
const assetPaths = info.assets.map(asset => {
  if (typeof asset.name !== "string" || basename(asset.name) !== asset.name || !expected.delete(asset.name)) {
    throw new Error("Unexpected or duplicate artifact filename.");
  }
  const path = join(directory, asset.name);
  const data = readFileSync(path);
  if (data.length !== asset.size || createHash("sha256").update(data).digest("hex") !== asset.sha256) {
    throw new Error(`Artifact digest/size mismatch: ${asset.name}.`);
  }
  return path;
});
if (expected.size) throw new Error("Required release assets are missing.");
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
  gh(["release", "edit", tag, "--repo", repo, "--draft", "--title", `txTrade ${state.version}`,
    "--notes-file", notesPath, ...(state.channel === "beta" ? ["--prerelease"] : ["--prerelease=false"])]);
  gh(["release", "upload", tag, ...assetPaths, "--repo", repo, "--clobber"]);
} else {
  gh(["release", "create", tag, ...assetPaths, "--repo", repo, "--verify-tag", "--draft",
    "--title", `txTrade ${state.version}`, "--notes-file", notesPath,
    ...(state.channel === "beta" ? ["--prerelease"] : [])]);
}
console.log(`Draft ${tag} is ready for review. It remains unpublished.`);
