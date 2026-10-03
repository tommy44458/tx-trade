import version from "../../../version.json";

export const VERSION = version.version;
export const REPOSITORY = "https://github.com/tommy44458/txin-trade";
export const WEB_APP = "https://app.txintrade.com";
export const CONTACT_EMAIL = "support@txintrade.com";
// The first signed release (1.0.0) is published on GitHub.
export const RELEASED = true;
/** The site worker counts the download, then sends it to the installer on GitHub (worker/index.js). */
export function downloadLink(platform: "mac" | "windows", from: "home" | "download", locale: Locale): string {
  return `/get/${platform}?from=${from}&lang=${locale}`;
}
export const RELEASES = `${REPOSITORY}/releases`;

export type Locale = "en" | "zh-TW";
export const LOCALES: Locale[] = ["en", "zh-TW"];

/** The same page in the given locale. */
export function localePath(path: string, locale: Locale): string {
  const bare = path.replace(/^\/zh-TW(?=\/|$)/, "") || "/";
  return locale === "en" ? bare : `/zh-TW${bare === "/" ? "" : bare}`;
}
