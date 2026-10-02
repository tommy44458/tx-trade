// Appearance and language chosen on the remote page stay in that browser; they never
// change the computer's settings. Storage can be unavailable (private browsing).

const KEYS = { theme: "txintrade.remote.theme", locale: "txintrade.remote.locale" } as const;

export function rememberBrowserPreference(kind: keyof typeof KEYS, value: string): void {
  try {
    window.localStorage.setItem(KEYS[kind], value);
  } catch {
    /* The choice still applies for this visit. */
  }
}

export function browserPreference(kind: keyof typeof KEYS): string | null {
  try {
    return window.localStorage.getItem(KEYS[kind]);
  } catch {
    return null;
  }
}
