// Refuse to deploy a site whose download buttons point at installers that are not published yet:
// the links follow version.json, which moves to the next version before its release is public.
import { readFileSync } from "node:fs";

const { version } = JSON.parse(readFileSync(new URL("../../../version.json", import.meta.url), "utf8"));
const base = "https://github.com/tommy44458/txin-trade/releases/latest/download";
let missing = false;
for (const asset of [`txinTrade-${version}-mac-arm64.dmg`, `txinTrade-${version}-win-x64.exe`]) {
  const response = await fetch(`${base}/${asset}`, { method: "HEAD", redirect: "follow" });
  if (!response.ok) {
    console.error(`${asset} is not published yet (${response.status}).`);
    missing = true;
  }
}
if (missing) {
  console.error("Publish the GitHub release first, then deploy the site.");
  process.exit(1);
}
console.log(`Downloads for ${version} are published.`);
