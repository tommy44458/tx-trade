const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("tradeHelper", Object.freeze({
  platform: process.platform,
  version: "0.2.0",
  openExternal: url => ipcRenderer.invoke("desktop:open-external", url),
  retryStartup: () => ipcRenderer.invoke("desktop:retry-startup"),
  updateLocale: locale => ipcRenderer.invoke("desktop:update-locale", locale),
  updateTheme: theme => ipcRenderer.invoke("desktop:update-theme", theme),
}));
