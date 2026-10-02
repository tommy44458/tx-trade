// Refuse to deploy a site whose download button points at an installer that is not published yet:
// the link follows version.json, which moves to the next version before its release is public.
import { readFileSync } from "node:fs";

const { version } = JSON.parse(readFileSync(new URL("../../../version.json", import.meta.url), "utf8"));
const url = `https://github.com/tommy44458/txin-trade/releases/latest/download/txinTrade-${version}-mac-arm64.dmg`;
const response = await fetch(url, { method: "HEAD", redirect: "follow" });
if (!response.ok) {
  console.error(`The download for ${version} is not published yet (${response.status}): ${url}`);
  console.error("Publish the GitHub release first, then deploy the site.");
  process.exit(1);
}
console.log(`Download for ${version} is published.`);
