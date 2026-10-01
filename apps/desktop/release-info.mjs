import { readFileSync } from "node:fs";
import { join } from "node:path";

// Release information comes only from the installed bundle (or the development
// checkout). Reading this never starts the backend or opens the user's database.
export function readReleaseInfo({ packaged, resourcesDir, repoDir, version, locale }) {
  const directory = packaged ? resourcesDir : repoDir;
  let channel = version.includes("-beta.") ? "beta" : "stable";
  try {
    const manifest = JSON.parse(readFileSync(join(directory, "version.json"), "utf8"));
    if (manifest.version === version && ["stable", "beta"].includes(manifest.channel)) {
      channel = manifest.channel;
    }
  } catch { /* About remains available if an older bundle lacks the manifest. */ }

  const filename = locale === "en-US" ? "CHANGELOG.en.md" : "CHANGELOG.md";
  let notes = "";
  let prepared = false;
  try {
    const changelog = readFileSync(join(directory, filename), "utf8").replace(/\r\n/g, "\n");
    const sections = [...changelog.matchAll(/^## \[([^\]\n]+)\]([^\n]*)$/gm)];
    const currentIndex = sections.findIndex(section => section[1] === version);
    const index = currentIndex >= 0 ? currentIndex
      : sections.findIndex(section => section[1] === "Unreleased");
    if (index >= 0) {
      const section = sections[index];
      notes = changelog.slice(section.index + section[0].length,
        sections[index + 1]?.index ?? changelog.length).trim();
      prepared = currentIndex >= 0 && /^ - \d{4}-\d{2}-\d{2}$/.test(section[2]);
    }
  } catch { /* Missing notes do not make the application unusable. */ }
  return { version, channel, prepared, notes };
}
