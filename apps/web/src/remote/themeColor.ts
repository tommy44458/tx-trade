// The browser chrome colour (theme-color) of the remote page. iOS also uses it
// behind the toolbar and past the page ends, so it must match the screen shown:
// the gate screens' deeper canvas, or the app's canvas inside the workspace.
import { UI_THEME_CHANGE_EVENT, currentUiTheme, resolveUiTheme } from "../uiTheme.ts";

const COLORS = {
  gate: { light: "#fbfbfd", dark: "#0a0a0b" },
  app: { light: "#f5f5f7", dark: "#151517" },
} as const;

let surface: keyof typeof COLORS = "app";
let listening = false;

function apply() {
  const dark = resolveUiTheme(currentUiTheme(), window.matchMedia("(prefers-color-scheme: dark)").matches) === "dark";
  const color = COLORS[surface][dark ? "dark" : "light"];
  // One unconditional tag: the media-scoped ones in remote.html cannot follow a theme chosen on the page.
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    if (meta.media) meta.remove();
  }
  let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.name = "theme-color";
    document.head.append(meta);
  }
  meta.content = color;
}

/** Call with "gate" while a screen before the workspace shows, "app" otherwise. */
export function setThemeSurface(next: keyof typeof COLORS): void {
  surface = next;
  if (!listening) {
    listening = true;
    window.addEventListener(UI_THEME_CHANGE_EVENT, apply);
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", apply);
  }
  apply();
}
