import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../index.css";
import "../App.css";
import "../Workspace.css";
import "../SettingsPanel.css";
import { setUiLocale } from "../i18n/index.ts";
import { initializeUiTheme } from "../uiTheme.ts";
import RemoteApp from "./RemoteApp.tsx";

initializeUiTheme();
// The phone page follows the browser language; the toggle in the header overrides it.
void setUiLocale(navigator.language.toLowerCase().startsWith("zh") ? "zh-TW" : "en-US");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RemoteApp />
  </StrictMode>,
);
