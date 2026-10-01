import { app, BrowserWindow, ipcMain, Menu, nativeTheme, shell } from "electron";
import { randomBytes } from "node:crypto";
import { createWriteStream, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { availablePort, backendCommand, backendEnvironment, developmentConfig, externalUrl,
  spawnBackend, stopBackend, waitForBackend } from "./runtime.mjs";
import { NATIVE_STRINGS, readSavedLocale, validateLocale } from "./locales.mjs";
import { readSavedTheme, validateTheme } from "./themes.mjs";

const repoDir = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const token = randomBytes(32).toString("hex");
let window;
let backend;
let origin;
let closing = false;
let starting = false;
let startupFailed = false;
let backendLog;
let uiLocale = "zh-TW";

app.setName("AI Trade Helper");
if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => {
  if (window?.isMinimized()) window.restore();
  window?.focus();
});

function trustedSender(event) {
  if (!window || event.sender !== window.webContents
      || event.senderFrame !== window.webContents.mainFrame) throw new Error("Invalid window");
}

function startupPage(failed = false) {
  const text = NATIVE_STRINGS[uiLocale];
  const title = failed ? text.failed : text.starting;
  const body = failed
    ? `<p>${text.failureBody}</p><p class="hint">${text.failureHint}</p>
       <button id="retry">${text.retry}</button><p id="error" role="alert"></p>`
    : `<span class="spinner" aria-hidden="true"></span><p role="status">${text.preparing}</p>`;
  return `<!doctype html><html lang="${uiLocale}"><head><meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <title>AI Trade Helper</title><style>
  :root{color-scheme:light dark;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
  body{margin:0;display:grid;place-items:center;min-height:100vh;background:light-dark(#f5f5f7,#171719);color:light-dark(#202124,#ededf0)}
  main{width:min(440px,calc(100vw - 64px));padding:32px}h1{font-size:24px;font-weight:600;margin:0 0 18px}
  p{font-size:14px;line-height:1.7;color:light-dark(#65656e,#a6a6af)}
  button{font:inherit;background:#007aff;color:white;border:0;border-radius:9px;padding:11px 18px;cursor:pointer;margin-top:12px}
  button:disabled{opacity:.6}.hint{font-size:12px}#error{color:#e35b53}
  .spinner{display:block;width:24px;height:24px;border:2px solid #8884;border-top-color:#007aff;border-radius:50%;animation:spin .8s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.spinner{animation:none}}
  </style></head><body><main><h1>${title}</h1>${body}</main>
  <script>document.querySelector('#retry')?.addEventListener('click',async()=>{
    const button=document.querySelector('#retry');button.disabled=true;
    try{await window.tradeHelper.retryStartup()}
    catch{document.querySelector('#error').textContent=${JSON.stringify(text.retryFailed)};button.disabled=false}
  });</script></body></html>`;
}

function updateNativeMenu() {
  const text = NATIVE_STRINGS[uiLocale];
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { label: text.settings, submenu: [
      { label: text.openData, click: () => {
        void shell.openPath(join(app.getPath("userData"), "data"));
      } },
      { label: text.openLog, click: () => {
        void shell.openPath(join(app.getPath("userData"), "backend.log"));
      } },
    ] },
    { role: "editMenu", label: text.edit }, { role: "viewMenu", label: text.view },
    { role: "windowMenu", label: text.window },
  ]));
}

async function showStartup(failed = false) {
  startupFailed = failed;
  await window.loadURL(`data:text/html;charset=UTF-8,${encodeURIComponent(startupPage(failed))}`);
  window.show();
}

async function startBackend() {
  if (starting) return;
  starting = true;
  try {
    await stopBackend(backend);
    await showStartup();
    const port = await availablePort();
    origin = `http://127.0.0.1:${port}`;
    const resourcesDir = process.resourcesPath;
    const webDir = app.isPackaged ? join(resourcesDir, "web") : join(repoDir, "apps/web/dist");
    const userData = app.getPath("userData");
    mkdirSync(join(userData, "data"), { recursive: true, mode: 0o700 });
    const legacyConfig = !app.isPackaged ? developmentConfig(repoDir) : {};
    // Existing encrypted PostgreSQL configuration files remain untouched. Native
    // startup always uses its own embedded SQLite file, without a vault read.
    const env = backendEnvironment({ config: legacyConfig, dataDir: join(userData, "data"),
      port, token, pythonPath: !app.isPackaged ? join(repoDir, "apps/api/src") : undefined });
    const spec = backendCommand({ packaged: app.isPackaged, resourcesDir, repoDir,
      pythonPath: process.env.TRADE_HELPER_PYTHON });
    backend = spawnBackend(spec, { port, webDir, env });
    backendLog ||= createWriteStream(join(userData, "backend.log"), { flags: "a", mode: 0o600 });
    backend.stdout.pipe(backendLog, { end: false });
    backend.stderr.pipe(backendLog, { end: false });
    let spawnError;
    backend.once("error", error => { spawnError = error; });
    await waitForBackend(backend, origin, token);
    if (spawnError) throw spawnError;
    const activeBackend = backend;
    backend.once("exit", () => {
      if (!closing && !starting && activeBackend === backend) {
        stopBackend(activeBackend).then(() => showStartup(true)).catch(() => {});
      }
    });
    await window.loadURL(origin);
    startupFailed = false;
    window.show();
  } catch {
    await stopBackend(backend);
    await showStartup(true);
  } finally { starting = false; }
}

app.whenReady().then(async () => {
  uiLocale = readSavedLocale(join(app.getPath("userData"), "data"),
                            process.env.APP_LOCAL_USER_ID || "local-demo");
  nativeTheme.themeSource = readSavedTheme(join(app.getPath("userData"), "data"),
                                         process.env.APP_LOCAL_USER_ID || "local-demo");
  window = new BrowserWindow({ width: 1280, height: 900, minWidth: 840, minHeight: 640,
    title: "AI Trade Helper", show: false,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#151517" : "#f5f5f7",
    webPreferences: { preload: join(dirname(fileURLToPath(import.meta.url)), "preload.cjs"),
      nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true,
      partition: "ai-trade-helper" },
  });
  const localSession = window.webContents.session;
  localSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  localSession.setPermissionCheckHandler(() => false);
  localSession.webRequest.onBeforeSendHeaders((details, callback) => {
    if (origin && new URL(details.url).origin === origin
      && details.webContentsId === window.webContents.id) {
      details.requestHeaders.Authorization = `Bearer ${token}`;
    }
    callback({ requestHeaders: details.requestHeaders });
  });
  localSession.webRequest.onHeadersReceived((details, callback) => {
    const responseHeaders = { ...details.responseHeaders };
    if (origin && new URL(details.url).origin === origin) {
      responseHeaders["Content-Security-Policy"] = [
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
      ];
    }
    callback({ responseHeaders });
  });
  window.webContents.on("will-navigate", (event, url) => {
    if (!origin || new URL(url).origin !== origin) event.preventDefault();
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    try { shell.openExternal(externalUrl(url)); } catch { /* Unsupported protocols are blocked. */ }
    return { action: "deny" };
  });
  ipcMain.handle("desktop:open-external", async (event, url) => {
    trustedSender(event);
    await shell.openExternal(externalUrl(url));
  });
  ipcMain.handle("desktop:retry-startup", async event => {
    trustedSender(event);
    if (!startupFailed) throw new Error("Startup is already running");
    void startBackend();
  });
  ipcMain.handle("desktop:update-locale", (event, locale) => {
    trustedSender(event);
    uiLocale = validateLocale(locale);
    updateNativeMenu();
  });
  ipcMain.handle("desktop:update-theme", (event, theme) => {
    trustedSender(event);
    nativeTheme.themeSource = validateTheme(theme);
    window.setBackgroundColor(nativeTheme.shouldUseDarkColors ? "#151517" : "#f5f5f7");
  });
  nativeTheme.on("updated", () => {
    if (window && !window.isDestroyed()) {
      window.setBackgroundColor(nativeTheme.shouldUseDarkColors ? "#151517" : "#f5f5f7");
    }
  });
  updateNativeMenu();
  await startBackend();
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", event => {
  if (closing) return;
  event.preventDefault();
  closing = true;
  stopBackend(backend).finally(() => {
    backendLog?.end();
    app.quit();
  });
});

// Terminal termination uses the same cleanup path as closing the application.
process.on("SIGTERM", () => app.quit());
process.on("SIGINT", () => app.quit());
