import { existsSync, lstatSync, renameSync } from "node:fs";
import { join } from "node:path";

// Builds before the txTrade rename kept their profile under this product name.
export const LEGACY_PRODUCT_NAME = "AI Trade Helper";

function present(path, fs) {
  try { fs.lstatSync(path); return true; } catch { return false; }
}

// Move the pre-rename profile once, before Chromium creates the new one. A legacy
// profile still locked by a running old build, or one that cannot be moved, stays
// in use so that local data is never split or lost.
export function resolveUserData({ appData, current,
  fs = { existsSync, lstatSync, renameSync } }) {
  const legacy = join(appData, LEGACY_PRODUCT_NAME);
  if (fs.existsSync(current) || !fs.existsSync(legacy)) return { path: current, migrated: false };
  if (present(join(legacy, "SingletonLock"), fs)) return { path: legacy, migrated: false };
  try {
    fs.renameSync(legacy, current);
    return { path: current, migrated: true };
  } catch {
    return { path: legacy, migrated: false };
  }
}
