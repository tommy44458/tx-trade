const { contextBridge, ipcRenderer } = require("electron");
const versionArgument = "--trade-helper-version=";
const version = process.argv.find(value => value.startsWith(versionArgument))?.slice(versionArgument.length);

contextBridge.exposeInMainWorld("tradeHelper", Object.freeze({
  platform: process.platform,
  version,
  openExternal: url => ipcRenderer.invoke("desktop:open-external", url),
  focusWindow: () => ipcRenderer.invoke("desktop:focus-window"),
  retryStartup: () => ipcRenderer.invoke("desktop:retry-startup"),
  updateLocale: locale => ipcRenderer.invoke("desktop:update-locale", locale),
  updateTheme: theme => ipcRenderer.invoke("desktop:update-theme", theme),
}));
