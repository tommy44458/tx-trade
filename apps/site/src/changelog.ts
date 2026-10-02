import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { marked } from "marked";
import type { Locale } from "./site";

/** Release notes from the repository's CHANGELOG, without its developer preamble. */
export function changelogHtml(locale: Locale): string {
  // Builds run from apps/site; the changelog lives at the repository root.
  const file = resolve(process.cwd(), "../..", locale === "en" ? "CHANGELOG.en.md" : "CHANGELOG.md");
  const text = readFileSync(file, "utf8");
  const releases = text.slice(text.indexOf("\n## ") + 1)
    // An empty Unreleased section (right after a release) is left out rather than shown as a bare heading.
    .replace(/^## \[Unreleased\]\s*(?=^## )/m, "")
    .replace(/^## \[Unreleased\]/m, locale === "en" ? "## Coming in the next release" : "## 即將推出")
    .replace(/^## \[([^\]]+)\] - (\d{4}-\d{2}-\d{2})$/gm, "## $1 · $2");
  return marked.parse(releases, { async: false }) as string;
}
