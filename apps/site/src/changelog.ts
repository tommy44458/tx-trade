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
    .replace(/^## \[Unreleased\]/m, locale === "en" ? "## Coming in the next release" : "## 即將推出");
  return marked.parse(releases, { async: false }) as string;
}
