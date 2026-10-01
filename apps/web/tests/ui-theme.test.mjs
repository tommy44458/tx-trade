import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  UI_THEME_CHANGE_EVENT,
  UI_THEMES,
  applyUiTheme,
  currentUiTheme,
  initializeUiTheme,
  isUiTheme,
  normalizeUiTheme,
  resolveUiTheme,
} from '../src/uiTheme.ts';
import { settingsRequest } from '../src/localSettings.ts';

test('appearance preferences validate without confusing an explicit choice with the system setting', () => {
  assert.deepEqual(UI_THEMES, ['system', 'light', 'dark']);
  for (const value of UI_THEMES) assert.equal(isUiTheme(value), true);
  for (const value of [undefined, null, '', 'DARK', 'auto', false]) {
    assert.equal(isUiTheme(value), false);
    assert.equal(normalizeUiTheme(value), 'system');
  }
  for (const systemDark of [true, false]) {
    assert.equal(resolveUiTheme('light', systemDark), 'light');
    assert.equal(resolveUiTheme('dark', systemDark), 'dark');
    assert.equal(resolveUiTheme('system', systemDark), systemDark ? 'dark' : 'light');
  }
});

test('saved settings drive the document and native theme, with no bootstrap override or failed-save preview', async () => {
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  const originalFetch = globalThis.fetch;
  const media = Object.assign(new EventTarget(), { matches: false });
  const nativeCalls = [];
  const events = [];
  const browser = Object.assign(new EventTarget(), {
    matchMedia: () => media,
    tradeHelper: { updateTheme: async theme => { nativeCalls.push(theme); } },
  });
  const document = { documentElement: { dataset: {} } };
  globalThis.window = browser;
  globalThis.document = document;
  browser.addEventListener(UI_THEME_CHANGE_EVENT, event => events.push(event.detail));
  try {
    initializeUiTheme();
    assert.deepEqual(document.documentElement.dataset, {});
    assert.deepEqual(nativeCalls, []);
    assert.deepEqual(events, []);

    applyUiTheme('dark');
    assert.equal(currentUiTheme(), 'dark');
    assert.deepEqual(events.at(-1), { preference: 'dark', resolved: 'dark' });
    assert.deepEqual(nativeCalls, ['dark']);
    applyUiTheme('dark');
    assert.equal(events.length, 1);
    assert.deepEqual(nativeCalls, ['dark']);
    media.matches = true;
    media.dispatchEvent(new Event('change'));
    assert.equal(events.length, 1, 'an explicit theme ignores OS changes');

    applyUiTheme('light');
    assert.deepEqual(events.at(-1), { preference: 'light', resolved: 'light' });
    applyUiTheme('system');
    assert.deepEqual(events.at(-1), { preference: 'system', resolved: 'dark' });
    const count = nativeCalls.length;
    media.matches = false;
    media.dispatchEvent(new Event('change'));
    assert.deepEqual(events.at(-1), { preference: 'system', resolved: 'light' });
    assert.equal(nativeCalls.length, count, 'an OS change does not overwrite the saved preference');

    const requests = [];
    globalThis.fetch = async (url, options) => {
      requests.push({ url, body: options?.body });
      return { ok: true, json: async () => ({ ui_theme: 'dark' }) };
    };
    const saved = await settingsRequest('/settings', { method: 'PATCH', body: JSON.stringify({ ui_theme: 'dark' }) });
    assert.equal(saved.ui_theme, 'dark');
    assert.equal(currentUiTheme(), 'dark');
    assert.deepEqual(requests, [{ url: '/api/v1/settings', body: '{"ui_theme":"dark"}' }]);
    const previousEvents = events.length;
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: { message: 'Fixture unavailable' } }) });
    await assert.rejects(settingsRequest('/settings', { method: 'PATCH', body: '{"ui_theme":"light"}' }), /Fixture unavailable/);
    assert.equal(currentUiTheme(), 'dark');
    assert.equal(events.length, previousEvents);

    globalThis.fetch = async () => ({ ok: true, json: async () => ({ model: 'legacy-settings' }) });
    await settingsRequest('/settings');
    assert.equal(currentUiTheme(), 'system', 'an older settings response defaults to the original system behavior');
    browser.tradeHelper.updateTheme = async () => { throw new Error('Fixture native IPC failure'); };
    applyUiTheme('light');
    await Promise.resolve();
    assert.equal(currentUiTheme(), 'light', 'native chrome cannot undo a server-confirmed document theme');
  } finally {
    globalThis.window = originalWindow;
    globalThis.document = originalDocument;
    globalThis.fetch = originalFetch;
  }
});
