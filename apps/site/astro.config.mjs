import { fileURLToPath } from "node:url";
import sitemap from "@astrojs/sitemap";
import { defineConfig } from "astro/config";

const repoRoot = fileURLToPath(new URL("../..", import.meta.url));

// The official site: English at /, Traditional Chinese at /zh-TW/.
export default defineConfig({
  site: "https://txintrade.com",
  // One file per page, served at URLs without a trailing slash (no redirect).
  trailingSlash: "never",
  // The stylesheet is small: inline it so nothing blocks the first paint.
  build: { format: "file", inlineStylesheets: "always" },
  i18n: {
    defaultLocale: "en",
    locales: ["en", "zh-TW"],
    routing: { prefixDefaultLocale: false },
  },
  integrations: [sitemap({ i18n: { defaultLocale: "en", locales: { en: "en", "zh-TW": "zh-TW" } } })],
  // Screenshots, the changelog and the version live at the repository root.
  // Never inline scripts: the CSP allows only same-origin script files.
  vite: { server: { fs: { allow: [repoRoot] } }, build: { assetsInlineLimit: 0 } },
});
