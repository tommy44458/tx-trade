import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { isUiLocale, setUiLocale, uiLocale, uiText } from '../src/i18n/index.ts';
import { enUS, textNamespace, zhTW } from '../src/i18n/resources.ts';
import { timeframeCode, timeframeLabel } from '../src/timeframes.ts';
import { validateLeverageInput } from '../src/leverage.ts';

afterEach(async () => { await setUiLocale('zh-TW'); });

const placeholders = text => [...text.matchAll(/{{\s*([^}]+)\s*}}/g)].map(match => match[1].trim()).sort();

test('published UI locales have complete namespace and interpolation parity', () => {
  assert.deepEqual(Object.keys(enUS).sort(), Object.keys(zhTW).sort());
  for (const [ns, translated] of Object.entries(enUS)) {
    assert.deepEqual(Object.keys(translated).sort(), Object.keys(zhTW[ns]).sort(), ns);
    for (const [key, text] of Object.entries(translated)) {
      assert.ok(text.trim(), `${ns}: ${key}`);
      assert.deepEqual(placeholders(text), placeholders(zhTW[ns][key]), `${ns}: ${key}`);
      assert.equal(textNamespace[key], ns);
    }
  }
});

test('legacy defaults are Chinese and selecting English changes future copy only', async () => {
  await setUiLocale('zh-TW');
  const savedReport = Object.freeze({ reason: '原始分析：守住 121.42–121.82 壓力帶。', output_locale: 'zh-TW' });
  assert.equal(uiText('市場分析'), '市場分析');
  await setUiLocale('en-US');
  assert.equal(uiLocale(), 'en-US');
  assert.equal(uiText('市場分析'), 'Market analysis');
  assert.equal(savedReport.reason, '原始分析：守住 121.42–121.82 壓力帶。');
  assert.equal(savedReport.output_locale, 'zh-TW');
});

test('interpolation retains exact user values and is not HTML escaping source data', async () => {
  await setUiLocale('en-US');
  assert.equal(uiText('最愛交易對未儲存：{{p0}}', { p0: 'HTTP 503 <retry>' }), 'Favorite markets were not saved: HTTP 503 <retry>');
  assert.equal(uiText('· {{p0}} 次確認轉折 · {{p1}} 次獨立觸及', { p0: 5, p1: 3 }), '· 5 confirmed pivots · 3 independent touches');
});

test('timeframe language changes labels without changing monthly and minute identifiers', async () => {
  await setUiLocale('en-US');
  assert.equal(timeframeLabel('1d'), 'Daily');
  assert.equal(timeframeLabel('1M'), 'Monthly');
  assert.equal(timeframeCode('1M'), '1M');
  assert.equal(timeframeCode('1m'), '1m');
  await setUiLocale('zh-TW');
  assert.equal(timeframeLabel('1M'), '月線');
});

test('leverage validation keeps the same value contract in both languages', async () => {
  const outcomes = [];
  for (const locale of ['zh-TW', 'en-US']) {
    await setUiLocale(locale);
    outcomes.push(['', '2.5', '0', '126', '117'].map(value => validateLeverageInput(value).value));
    assert.ok(validateLeverageInput('').error);
  }
  assert.deepEqual(outcomes[0], outcomes[1]);
});

test('unsupported settings locale is rejected by the shared language guard', () => {
  assert.equal(isUiLocale('en-US'), true);
  assert.equal(isUiLocale('zh-TW'), true);
  assert.equal(isUiLocale('en'), false);
  assert.equal(isUiLocale(undefined), false);
});

test('English position and evidence counts use singular and plural without altering quantities', async () => {
  await setUiLocale('en-US');
  assert.equal(uiText('positionCount', { count: 1 }), '1 position');
  assert.equal(uiText('positionCount', { count: 3 }), '3 positions');
  assert.equal(uiText('evidenceCount', { count: 1 }), '1 evidence item');
  assert.equal(uiText('evidenceCount', { count: 0 }), '0 evidence items');
  await setUiLocale('zh-TW');
  assert.equal(uiText('positionCount', { count: 3 }), '3 筆持倉');
});

test('product copy and its registered keys describe calculations without implementation-language labels', () => {
  for (const [locale, resources] of [['zh-TW', zhTW], ['en-US', enUS]]) {
    for (const [namespace, entries] of Object.entries(resources)) {
      for (const [key, text] of Object.entries(entries)) {
        assert.equal(/python/i.test(key), false, `${locale}/${namespace}: ${key}`);
        assert.equal(/python/i.test(text), false, `${locale}/${namespace}: ${text}`);
      }
    }
  }
});

test('bulk position controls have paired translations and retain the selected and total counts', async () => {
  await setUiLocale('en-US');
  assert.equal(uiText('全選持倉'), 'Select all positions');
  assert.equal(uiText('清除選取'), 'Clear selection');
  assert.equal(uiText('已選 {{p0}}／{{p1}} 筆', { p0: 12, p1: 14 }), 'Selected 12 of 14 positions');
  await setUiLocale('zh-TW');
  assert.equal(uiText('已選 {{p0}}／{{p1}} 筆', { p0: 12, p1: 14 }), '已選 12／14 筆');
});
