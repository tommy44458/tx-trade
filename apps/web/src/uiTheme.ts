export const UI_THEMES = ["system", "light", "dark"] as const;
export type UiTheme = (typeof UI_THEMES)[number];
export type ResolvedUiTheme = "light" | "dark";
export const UI_THEME_CHANGE_EVENT = "trade-helper:theme-change";
export type UiThemeChange = { preference: UiTheme; resolved: ResolvedUiTheme };

export const isUiTheme = (value: unknown): value is UiTheme =>
  value === "system" || value === "light" || value === "dark";
export const normalizeUiTheme = (value: unknown): UiTheme => isUiTheme(value) ? value : "system";
export const resolveUiTheme = (preference: UiTheme, systemDark: boolean): ResolvedUiTheme =>
  preference === "system" ? systemDark ? "dark" : "light" : preference;

let media: MediaQueryList | null = null;
let lastChange: UiThemeChange | null = null;

export function currentUiTheme(): UiTheme {
  return typeof document === "undefined" ? "system" : normalizeUiTheme(document.documentElement.dataset.theme);
}

function notifyThemeChange(preference: UiTheme) {
  if (typeof window === "undefined") return;
  const resolved = resolveUiTheme(preference, media?.matches ?? false);
  if (lastChange?.preference === preference && lastChange.resolved === resolved) return;
  lastChange = { preference, resolved };
  window.dispatchEvent(new CustomEvent<UiThemeChange>(UI_THEME_CHANGE_EVENT, { detail: lastChange }));
}

/** Bootstrap follows CSS/the native window until authoritative settings arrive. */
export function initializeUiTheme(): void {
  if (typeof window === "undefined" || media) return;
  media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", () => {
    if (currentUiTheme() === "system") notifyThemeChange("system");
  });
}

/** Apply a server-confirmed preference; persistence stays in the settings API. */
export function applyUiTheme(preference: UiTheme): void {
  if (typeof document === "undefined") return;
  initializeUiTheme();
  const changed = document.documentElement.dataset.theme !== preference;
  document.documentElement.dataset.theme = preference;
  notifyThemeChange(preference);
  if (changed && typeof window !== "undefined") {
    // A native-chrome failure must not turn a successfully saved setting into an
    // API failure. The document already displays the confirmed theme.
    void window.tradeHelper?.updateTheme?.(preference).catch(() => {});
  }
}
