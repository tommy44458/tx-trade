import { existsSync, mkdirSync, readlinkSync, renameSync } from "node:fs";
import { join } from "node:path";

// Earlier product names, newest first. Development builds used "txTrade";
// the first local builds used "AI Trade Helper".
export const LEGACY_PRODUCT_NAMES = ["txTrade", "AI Trade Helper"];
const DATABASE = join("data", "trade_helper.sqlite3");
// Application data that belongs to the user. Chromium caches are left behind.
const CARRIED = ["data", "Partitions"];

const realFs = { existsSync, mkdirSync, readlinkSync, renameSync };

function processAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { return error.code === "EPERM"; }
}

// Chromium's SingletonLock is a symlink to "<host>-<pid>". A lock left by a
// crashed or force-quit build points at a process that no longer exists.
function lockedByRunningBuild(directory, fs, alive) {
  let target;
  try { target = fs.readlinkSync(join(directory, "SingletonLock")); } catch { return false; }
  const pid = Number(String(target).split("-").at(-1));
  return Number.isInteger(pid) && pid > 0 ? alive(pid) : true;
}

// Electron may create the new profile directory before this runs, so the
// decision is based on whether the user's database already lives there. The
// newest legacy profile that has a database moves its data and renderer
// storage across once; older profiles are left untouched. A profile still in
// use by a running old build, or one that cannot be moved, stays in use so that
// local data is never split or lost.
export function resolveUserData({ appData, current, fs = realFs, alive = processAlive }) {
  if (fs.existsSync(join(current, DATABASE))) return { path: current, migrated: false };
  const legacy = LEGACY_PRODUCT_NAMES.map(name => join(appData, name))
    .find(path => fs.existsSync(join(path, DATABASE)));
  if (!legacy) return { path: current, migrated: false };
  if (lockedByRunningBuild(legacy, fs, alive)) return { path: legacy, migrated: false };
  try {
    fs.mkdirSync(current, { recursive: true });
    for (const name of CARRIED) {
      const source = join(legacy, name);
      const target = join(current, name);
      if (!fs.existsSync(source)) continue;
      // A database-less "data" folder here was created before migration; keep it aside.
      if (fs.existsSync(target)) fs.renameSync(target, `${target}.before-migration-${Date.now()}`);
      fs.renameSync(source, target);
    }
    return { path: current, migrated: true, from: legacy };
  } catch {
    return fs.existsSync(join(current, DATABASE))
      ? { path: current, migrated: true, from: legacy }
      : { path: legacy, migrated: false };
  }
}
