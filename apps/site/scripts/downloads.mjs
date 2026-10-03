// Daily download counts: `pnpm downloads [days]` (default 14).
// "site" counts download buttons chosen on txintrade.com; "github" is the daily change in GitHub's
// lifetime counts, where .zip (Mac) and .exe (Windows) also include in-app updates.
import { execFileSync } from "node:child_process";

const days = Number.parseInt(process.argv[2] ?? "14", 10);
if (!Number.isInteger(days) || days < 1) throw new Error("Usage: pnpm downloads [days]");
const since = `date('now', '-${days - 1} days')`;
const queries = {
  "Site downloads by day": `SELECT day, platform, SUM(count) AS downloads FROM site_downloads
    WHERE day >= ${since} GROUP BY day, platform ORDER BY day DESC, platform`,
  "Site downloads by source, language and country": `SELECT platform, source, locale, country, SUM(count) AS downloads
    FROM site_downloads WHERE day >= ${since} GROUP BY 1, 2, 3, 4 ORDER BY downloads DESC LIMIT 30`,
  "GitHub downloads by day": `SELECT today.day, today.asset, today.total - COALESCE(before.total, 0) AS downloads, today.total
    FROM github_downloads today LEFT JOIN github_downloads before
      ON before.asset = today.asset AND before.day = date(today.day, '-1 day')
    WHERE today.day >= ${since} AND today.total > COALESCE(before.total, 0)
    ORDER BY today.day DESC, today.asset`,
};
for (const [title, sql] of Object.entries(queries)) {
  const output = execFileSync("npx", ["--yes", "wrangler@4", "d1", "execute", "txintrade-site", "--remote", "--json",
    "--command", sql.replace(/\s+/g, " ")], { encoding: "utf8" });
  console.log(`\n${title}`);
  console.table(JSON.parse(output)[0].results);
}
