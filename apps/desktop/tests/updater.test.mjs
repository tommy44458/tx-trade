import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { createDesktopUpdater } from "../updater.mjs";

const policy = { enabled: true, signed: true, platform: "darwin", arch: "arm64", channel: "stable" };
const update = { version: "0.3.0", releaseNotes: "<p>New feature</p>" };
const ready = { ready: true, active_tasks: 0, backup_path: "/fake/backups/before-update.sqlite3" };

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function flush() {
  for (let count = 0; count < 12; count += 1) await Promise.resolve();
}

function clock() {
  let time = 0;
  let next = 0;
  const pending = new Map();
  return {
    setTimeout(fn, delay) {
      const id = ++next;
      pending.set(id, { fn, due: time + delay, delay });
      return id;
    },
    clearTimeout(id) { pending.delete(id); },
    now: () => time,
    get pending() { return [...pending.values()]; },
    runNext() {
      const entry = [...pending.entries()].sort((a, b) => a[1].due - b[1].due)[0];
      assert.ok(entry, "a timer is scheduled");
      pending.delete(entry[0]);
      time = entry[1].due;
      entry[1].fn();
    },
  };
}

function fixture(options = {}) {
  const app = Object.assign(new EventEmitter(), { isPackaged: true, getVersion: () => "0.2.0" });
  const engine = new EventEmitter();
  const calls = [];
  const states = [];
  const timers = clock();
  engine.checkForUpdates = async () => {
    calls.push("check");
    engine.emit("update-available", update);
    return { updateInfo: update };
  };
  engine.downloadUpdate = async () => {
    calls.push("download");
    engine.emit("update-downloaded", update);
    return ["/fake/update.zip"];
  };
  engine.quitAndInstall = (...args) => {
    calls.push(["quitAndInstall", ...args]);
    app.emit("before-quit");
  };
  const updater = createDesktopUpdater({
    app, autoUpdater: engine, platform: "darwin", arch: "arm64", distributionPolicy: policy,
    prepareUpdate: async () => { calls.push("prepare"); return ready; },
    cancelUpdate: async () => { calls.push("cancel"); },
    stopBackend: async () => { calls.push("stop"); },
    onState: state => { states.push(state); }, timers, now: timers.now, random: () => 0.5,
    ...options,
  });
  return { app, engine, calls, states, timers, updater };
}

async function downloaded(fixture) {
  await fixture.updater.check();
  await fixture.updater.download();
  assert.equal(fixture.updater.state.status, "downloaded");
}

test("missing, unsigned, mismatched, development and unsupported distributions never use the engine", async () => {
  const invalid = [
    { distributionPolicy: null },
    { distributionPolicy: { ...policy, enabled: false } },
    { distributionPolicy: { ...policy, signed: false } },
    { distributionPolicy: { enabled: true } },
    { distributionPolicy: { ...policy, channel: "beta" } },
    { distributionPolicy: { ...policy, platform: "win32" } },
    { distributionPolicy: { ...policy, arch: "x64" } },
    { platform: "win32" },
    { arch: "x64" },
    { currentVersion: "0.2.0-rc.1" },
    { app: { isPackaged: false, getVersion: () => "0.2.0" } },
    { autoUpdater: undefined },
  ];
  for (const options of invalid) {
    const f = fixture(options);
    assert.equal(f.updater.state.enabled, false);
    assert.equal(f.states.length, 0, "factory does not invoke callbacks during assignment");
    await f.updater.start();
    await f.updater.check();
    await f.updater.download();
    await f.updater.install();
    await f.updater.cancel();
    await f.updater.dispose();
    assert.equal(f.updater.state.status, "disabled");
    assert.equal(f.calls.length, 0);
    assert.equal(f.timers.pending.length, 0);
    assert.equal(f.engine.listenerCount("error"), 0);
  }
});

const windowsPolicy = { enabled: true, signed: false, platform: "win32", arch: "x64", channel: "stable" };

test("an unsigned Windows x64 release updates, installing silently and relaunching", async () => {
  const f = fixture({ platform: "win32", arch: "x64", distributionPolicy: windowsPolicy });
  assert.equal(f.updater.state.enabled, true);
  await downloaded(f);
  await f.updater.install();
  assert.deepEqual(f.calls, ["check", "download", "prepare", "stop", ["quitAndInstall", true, true]]);
});

test("Windows test builds, other Windows architectures and unsigned macOS builds never update", () => {
  for (const options of [
    { platform: "win32", arch: "x64", distributionPolicy: { ...windowsPolicy, enabled: false } },
    { platform: "win32", arch: "arm64", distributionPolicy: { ...windowsPolicy, arch: "arm64" } },
    { platform: "win32", arch: "x64", distributionPolicy: { ...windowsPolicy, platform: "darwin" } },
    { platform: "win32", arch: "x64", distributionPolicy: { ...windowsPolicy, channel: "beta" } },
    { platform: "darwin", arch: "arm64", distributionPolicy: { ...policy, signed: false } },
  ]) {
    assert.equal(fixture(options).updater.state.enabled, false, JSON.stringify(options));
  }
});

test("signed builds configure bundled metadata, explicit download and installation, without enabling downgrades", () => {
  for (const channel of ["stable", "beta"]) {
    const engine = new EventEmitter();
    const assignments = [];
    for (const name of ["channel", "allowPrerelease", "allowDowngrade"]) {
      Object.defineProperty(engine, name, { set: value => assignments.push([name, value]) });
    }
    engine.setFeedURL = () => assert.fail("controller cannot replace bundled provider URL");
    const f = fixture({ autoUpdater: engine, currentVersion: channel === "beta" ? "0.2.0-beta.1" : "0.2.0",
      distributionPolicy: { ...policy, channel } });
    assert.equal(f.updater.state.enabled, true);
    assert.deepEqual(assignments, [["channel", channel === "beta" ? "beta" : "latest"],
      ["allowPrerelease", channel === "beta"], ["allowDowngrade", false]]);
    assert.equal(engine.autoDownload, false);
    assert.equal(engine.autoInstallOnAppQuit, false);
    assert.equal(engine.forceDevUpdateConfig, false);
    assert.equal(engine.logger, null);
  }
});

test("startup is delayed, checks coalesce and periodic failures back off", async () => {
  const f = fixture();
  const pending = deferred();
  f.engine.checkForUpdates = () => { f.calls.push("check"); return pending.promise; };
  await f.updater.start();
  await f.updater.start();
  assert.equal(f.calls.length, 0);
  assert.equal(f.timers.pending.length, 1);
  assert.ok(f.timers.pending[0].delay >= 5000 && f.timers.pending[0].delay <= 15000);
  f.timers.runNext();
  await flush();
  const first = f.updater.check();
  assert.equal(first, f.updater.check());
  assert.deepEqual(f.calls, ["check"]);
  pending.reject(new Error("secret URL and token"));
  await first;
  await flush();
  assert.equal(f.updater.state.errorCode, "UPDATE_CHECK_FAILED");
  assert.equal(f.timers.pending[0].delay, 15 * 60 * 1000);
  f.engine.checkForUpdates = () => { f.calls.push("check"); throw new Error("offline"); };
  f.timers.runNext();
  await flush();
  assert.equal(f.timers.pending[0].delay, 30 * 60 * 1000);
  f.engine.checkForUpdates = async () => ({ updateInfo: update });
  f.timers.runNext();
  await flush();
  assert.equal(f.updater.state.status, "available");
  assert.equal(f.timers.pending[0].delay, 6 * 60 * 60 * 1000);
  await f.updater.dispose();
  assert.equal(f.timers.pending.length, 0);
});

test("synchronous engine failures do not leave checks or downloads permanently occupied", async () => {
  const f = fixture();
  f.engine.checkForUpdates = () => { throw new Error("offline"); };
  await f.updater.check();
  f.engine.checkForUpdates = async () => ({ updateInfo: update });
  await f.updater.check();
  assert.equal(f.updater.state.status, "available");
  f.engine.downloadUpdate = () => { throw new Error("offline"); };
  await f.updater.download();
  f.engine.downloadUpdate = async () => { f.engine.emit("update-downloaded", update); };
  await f.updater.download();
  assert.equal(f.updater.state.status, "downloaded");
});

test("recurring checks vary by ten percent while the explicit startup delay stays intact", async () => {
  for (const value of [0, 1]) {
    const f = fixture({ random: () => value, startupDelayMs: 50 });
    await f.updater.start();
    assert.equal(f.timers.pending[0].delay, 50);
    f.timers.runNext();
    await flush();
    assert.equal(f.timers.pending[0].delay, Math.round(6 * 60 * 60 * 1000 * (0.9 + value * 0.2)));
    await f.updater.dispose();
  }
});

test("stable builds reject beta, old and malformed update events before any download", async () => {
  for (const version of ["0.2.0", "0.1.9", "0.3.0-beta.1", "999.0.0-rc.1", "arbitrary"] ) {
    const f = fixture();
    f.engine.checkForUpdates = async () => {
      const info = { version };
      f.engine.emit("update-available", info);
      return { updateInfo: info };
    };
    await f.updater.check();
    await f.updater.download();
    assert.equal(f.updater.state.errorCode, "UPDATE_VERSION_REJECTED");
    assert.equal(f.updater.state.canInstall, false);
    assert.equal(f.calls.length, 0);
  }
});

test("beta builds accept later betas and the matching stable version", async () => {
  for (const version of ["0.2.0-beta.2", "0.2.0", "0.3.0-beta.0"]) {
    const f = fixture({ currentVersion: "0.2.0-beta.1", distributionPolicy: { ...policy, channel: "beta" } });
    f.engine.checkForUpdates = async () => ({ updateInfo: { version } });
    await f.updater.check();
    assert.equal(f.updater.state.version, version);
    assert.equal(f.updater.state.status, "available");
  }
});

test("downloads are explicit, progress bounded, notes plain text and installation never happens on download", async () => {
  const f = fixture();
  await f.updater.check();
  assert.deepEqual(f.calls, ["check"]);
  assert.equal(f.updater.state.releaseNotes, "New feature");
  const pending = deferred();
  f.engine.downloadUpdate = () => { f.calls.push("download"); return pending.promise; };
  const task = f.updater.download();
  assert.equal(task, f.updater.download());
  await flush();
  f.engine.emit("download-progress", { percent: 120 });
  assert.equal(f.updater.state.percent, 100);
  f.engine.emit("download-progress", { percent: -10 });
  assert.equal(f.updater.state.percent, 0);
  f.engine.emit("update-downloaded", { ...update, releaseNotes: [{ note: "<h1>Ready</h1>" }] });
  pending.resolve([]);
  await task;
  assert.equal(f.updater.state.status, "downloaded");
  assert.equal(f.updater.state.releaseNotes, "Ready");
  assert.equal(f.updater.state.canInstall, true);
  assert.deepEqual(f.calls, ["check", "download"]);
  await f.updater.check();
  assert.deepEqual(f.calls, ["check", "download"]);
  assert.ok(Object.isFrozen(f.updater.state));
});

test("unexpected, incomplete and signature-invalid downloads cannot install", async () => {
  const f = fixture();
  f.engine.emit("update-downloaded", update);
  await assert.rejects(f.updater.install(), { code: "UPDATE_NOT_DOWNLOADED" });
  await f.updater.check();
  f.engine.downloadUpdate = async () => [];
  await f.updater.download();
  assert.equal(f.updater.state.canInstall, false);
  f.engine.downloadUpdate = async () => {
    f.engine.emit("error", Object.assign(new Error("sensitive path"), { code: "ERR_UPDATER_INVALID_SIGNATURE" }));
    f.engine.emit("update-downloaded", update);
  };
  await f.updater.download();
  assert.equal(f.updater.state.errorCode, "UPDATE_SIGNATURE_INVALID");
  assert.equal(f.updater.state.canInstall, false);
  await assert.rejects(f.updater.install(), { code: "UPDATE_NOT_DOWNLOADED" });
  assert.ok(!f.calls.includes("stop"));
});

test("a package for another version is rejected even after download starts", async () => {
  const f = fixture();
  await f.updater.check();
  f.engine.downloadUpdate = async () => f.engine.emit("update-downloaded", { version: "0.4.0" });
  await f.updater.download();
  assert.equal(f.updater.state.errorCode, "UPDATE_VERSION_REJECTED");
  assert.equal(f.updater.state.canInstall, false);
});

test("installation holds the backend gate and waits for every task and a verified backup", async () => {
  let f;
  let count = 0;
  f = fixture({ prepareUpdate: async () => {
    f.calls.push("prepare");
    count += 1;
    return count === 1 ? { ready: false, active_tasks: 7, task_counts: { report: 3, batch: 4 } } : ready;
  } });
  await downloaded(f);
  const task = f.updater.install();
  assert.equal(task, f.updater.install());
  await flush();
  assert.equal(f.updater.state.status, "waiting-for-idle");
  assert.equal(f.updater.state.activeTasks, 7);
  assert.deepEqual(f.calls, ["check", "download", "prepare"]);
  f.timers.runNext();
  await task;
  assert.equal(f.updater.state.status, "installing");
  assert.equal(f.updater.state.canInstall, false);
  assert.deepEqual(f.calls, ["check", "download", "prepare", "prepare", "stop", ["quitAndInstall", false, true]]);
});

test("update events cannot overwrite installation state during an awaited backend drain", async () => {
  const pending = deferred();
  const f = fixture({ prepareUpdate: () => pending.promise });
  await downloaded(f);
  const task = f.updater.install();
  await flush();
  for (const [name, payload] of [["checking-for-update"], ["update-available", { version: "9.0.0" }],
    ["update-not-available"], ["download-progress", { percent: 1 }], ["update-downloaded", { version: "9.0.0" }]]) {
    f.engine.emit(name, payload);
  }
  assert.equal(f.updater.state.status, "waiting-for-idle");
  assert.equal(f.updater.state.version, "0.3.0");
  pending.resolve(ready);
  await task;
  assert.equal(f.updater.state.status, "installing");
});

test("cancellation waits for pending prepare before reopening the gate and retains the downloaded update", async () => {
  const pending = deferred();
  let f;
  f = fixture({ prepareUpdate: () => { f.calls.push("prepare"); return pending.promise; } });
  await downloaded(f);
  const task = f.updater.install();
  await flush();
  const cancel = f.updater.cancel();
  await flush();
  assert.ok(!f.calls.includes("cancel"));
  pending.resolve(ready);
  await Promise.all([task, cancel]);
  assert.deepEqual(f.calls, ["check", "download", "prepare", "cancel"]);
  assert.equal(f.updater.state.status, "downloaded");
  assert.equal(f.updater.state.canInstall, true);
});

test("cancellation wakes the idle poll and retry starts a fresh guarded attempt", async () => {
  let f;
  let busy = true;
  f = fixture({ prepareUpdate: async () => {
    f.calls.push("prepare");
    return busy ? { ready: false, active_tasks: 1 } : ready;
  } });
  await downloaded(f);
  const task = f.updater.install();
  await flush();
  assert.equal(f.timers.pending.length, 1);
  await f.updater.cancel();
  await task;
  assert.equal(f.timers.pending.length, 0);
  assert.equal(f.updater.state.status, "downloaded");
  busy = false;
  await f.updater.install();
  assert.deepEqual(f.calls, ["check", "download", "prepare", "cancel", "prepare", "stop", ["quitAndInstall", false, true]]);
});

test("missing backend, malformed responses and failed backup never stop the backend or install", async () => {
  const scenarios = [
    { options: { prepareUpdate: undefined }, code: "UPDATE_BACKEND_UNAVAILABLE", cancelled: false },
    { options: { prepareUpdate: async () => null }, code: "UPDATE_PREPARE_FAILED", cancelled: true },
    { options: { prepareUpdate: async () => ({ ready: false, active_tasks: 0 }) }, code: "UPDATE_PREPARE_FAILED", cancelled: true },
    { options: { prepareUpdate: async () => ({ ready: true, active_tasks: 1, backup_path: "fake" }) }, code: "UPDATE_PREPARE_FAILED", cancelled: true },
    { options: { prepareUpdate: async () => ({ ready: true, active_tasks: 0 }) }, code: "UPDATE_BACKUP_FAILED", cancelled: true },
    { options: { prepareUpdate: async () => { throw Object.assign(new Error("database path"), { code: "UPDATE_BACKUP_FAILED" }); } }, code: "UPDATE_BACKUP_FAILED", cancelled: true },
  ];
  for (const scenario of scenarios) {
    const f = fixture(scenario.options);
    await downloaded(f);
    await assert.rejects(f.updater.install(), error => error.code === scenario.code && error.message === scenario.code);
    assert.equal(f.updater.state.status, "error");
    assert.equal(f.updater.state.errorCode, scenario.code);
    assert.equal(f.updater.state.canInstall, true);
    assert.equal(f.calls.includes("cancel"), scenario.cancelled);
    assert.ok(!f.calls.includes("stop"));
    assert.ok(!f.calls.some(call => Array.isArray(call)));
    await assert.rejects(f.updater.install(), { code: scenario.code });
  }
});

test("failed cancellation retains its error code and can retry releasing the gate", async () => {
  let shouldFail = true;
  const f = fixture({ prepareUpdate: async () => ({ ready: false, active_tasks: 2 }),
    cancelUpdate: async () => { if (shouldFail) throw new Error("offline and private path"); } });
  await downloaded(f);
  const task = f.updater.install();
  const rejection = assert.rejects(task, { code: "UPDATE_CANCEL_FAILED" });
  await flush();
  await assert.rejects(f.updater.cancel(), { code: "UPDATE_CANCEL_FAILED" });
  await rejection;
  assert.equal(f.updater.state.errorCode, "UPDATE_CANCEL_FAILED");
  shouldFail = false;
  await f.updater.cancel();
  assert.equal(f.updater.state.status, "downloaded");
});

test("native installation stays pending and late signature errors reach install.catch", async () => {
  const f = fixture();
  await downloaded(f);
  f.engine.quitAndInstall = () => f.calls.push("native-start");
  let settled = false;
  const task = f.updater.install();
  const rejection = assert.rejects(task, { code: "UPDATE_SIGNATURE_INVALID" }).then(() => { settled = true; });
  await flush();
  assert.equal(f.updater.state.status, "installing");
  assert.equal(settled, false);
  assert.ok(f.calls.includes("stop"));
  f.engine.emit("error", new Error("Code signature verification failed for /private/user/Downloads/fake.app"));
  await rejection;
  assert.equal(f.updater.state.status, "error");
  assert.equal(f.updater.state.errorCode, "UPDATE_SIGNATURE_INVALID");
  assert.equal(f.updater.state.canInstall, false);
  assert.ok(!f.calls.includes("cancel"), "stopped backend has no remaining gate; recovery starts a fresh backend");
  assert.equal(f.app.listenerCount("before-quit"), 0);
});

test("ordinary native failures after handoff and synchronous quit failures remain recoverable", async () => {
  for (const synchronous of [false, true]) {
    const f = fixture();
    await downloaded(f);
    f.engine.quitAndInstall = () => { if (synchronous) throw new Error("native failure"); };
    const task = f.updater.install();
    const rejection = assert.rejects(task, { code: "UPDATE_INSTALL_FAILED" });
    await flush();
    if (!synchronous) f.engine.emit("error", new Error("native failure with private URL"));
    await rejection;
    assert.equal(f.updater.state.status, "error");
    assert.equal(f.updater.state.canInstall, true);
    assert.equal(f.app.listenerCount("before-quit"), 0);
  }
});

test("failed shutdown releases the gate and never requests native installation", async () => {
  const f = fixture({ stopBackend: async () => { throw new Error("backend still alive"); } });
  await downloaded(f);
  await assert.rejects(f.updater.install(), { code: "UPDATE_INSTALL_FAILED" });
  assert.ok(f.calls.includes("cancel"));
  assert.ok(!f.calls.some(call => Array.isArray(call)));
});

test("disposing while waiting awaits gate release and prevents late responses from installing", async () => {
  const pending = deferred();
  const release = deferred();
  let f;
  f = fixture({ prepareUpdate: () => pending.promise,
    cancelUpdate: () => { f.calls.push("cancel"); return release.promise; } });
  await downloaded(f);
  const task = f.updater.install();
  await flush();
  const disposal = f.updater.dispose();
  pending.resolve(ready);
  await flush();
  assert.deepEqual(f.calls, ["check", "download", "cancel"]);
  release.resolve();
  await Promise.all([task, disposal]);
  assert.equal(f.updater.state.status, "disabled");
  assert.equal(f.updater.state.canInstall, false);
  assert.equal(f.timers.pending.length, 0);
  assert.equal(f.engine.listenerCount("update-available"), 0);
});

test("disposing during shutdown or native verification cannot hang or request another installation", async () => {
  for (const duringShutdown of [false, true]) {
    const stopped = deferred();
    const f = fixture({ stopBackend: () => stopped.promise });
    await downloaded(f);
    f.engine.quitAndInstall = () => f.calls.push("native-start");
    const task = f.updater.install();
    await flush();
    if (!duringShutdown) { stopped.resolve(); await flush(); }
    const disposal = f.updater.dispose();
    stopped.resolve();
    await Promise.all([task, disposal]);
    assert.equal(f.updater.state.status, "disabled");
    assert.equal(f.calls.includes("native-start"), !duringShutdown);
    assert.equal(f.app.listenerCount("before-quit"), 0);
    if (!duringShutdown) {
      assert.doesNotThrow(() => f.engine.emit("error", new Error("late native shutdown error")));
      assert.equal(f.updater.state.status, "disabled");
    }
  }
});

test("dispose ignores stale network events but retains an error sink until the request settles", async () => {
  const pending = deferred();
  const f = fixture();
  f.engine.checkForUpdates = () => pending.promise;
  const task = f.updater.check();
  await flush();
  await f.updater.dispose();
  assert.equal(f.engine.listenerCount("error"), 1);
  f.engine.emit("error", new Error("late offline failure"));
  f.engine.emit("update-available", update);
  pending.reject(new Error("late network error"));
  await task;
  await flush();
  assert.equal(f.updater.state.status, "disabled");
  assert.equal(f.engine.listenerCount("error"), 0);
});
