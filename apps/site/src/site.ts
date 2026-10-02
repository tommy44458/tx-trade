import version from "../../../version.json";

export const VERSION = version.version;
export const REPOSITORY = "https://github.com/tommy44458/txin-trade";
export const WEB_APP = "https://app.txintrade.com";
export const CONTACT_EMAIL = "support@txintrade.com";
// Turn on once the first signed release is published on GitHub.
export const RELEASED = false;
export const DOWNLOAD_MAC = `${REPOSITORY}/releases/latest/download/txinTrade-${VERSION}-mac-arm64.dmg`;
export const RELEASES = `${REPOSITORY}/releases`;

export type Locale = "en" | "zh-TW";
export const LOCALES: Locale[] = ["en", "zh-TW"];

/** The same page in the given locale. */
export function localePath(path: string, locale: Locale): string {
  const bare = path.replace(/^\/zh-TW(?=\/|$)/, "") || "/";
  return locale === "en" ? bare : `/zh-TW${bare === "/" ? "" : bare}`;
}
