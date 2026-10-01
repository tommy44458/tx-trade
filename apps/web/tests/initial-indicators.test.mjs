import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { setUiLocale } from '../src/i18n/index.ts';
import {
  initialIndicatorCatalog,
  initialIndicatorCopy,
  initialIndicatorParameterBadges,
  normalizeInitialIndicators,
  sameInitialIndicators,
} from '../src/initialIndicators.ts';

afterEach(async () => { await setUiLocale('zh-TW'); });

test('an older settings response still exposes the seven optional tools with the agreed defaults', () => {
  const catalog = initialIndicatorCatalog(undefined);
  assert.deepEqual(catalog.map(item => item.name), [
    'bollinger', 'fibonacci', 'adx_dmi', 'obv', 'donchian', 'keltner', 'stochastic',
  ]);
  assert.deepEqual(catalog.find(item => item.name === 'fibonacci').parameters,
    { lookback: 160, width: 3, direction: 'auto' });
  assert.deepEqual(catalog.find(item => item.name === 'keltner').parameters,
    { period: 20, atr_period: 14, multiplier: 2 });
});

test('the server catalog controls availability and displayed parameters', () => {
  const server = [{ name: 'bollinger', tool: 'bollinger', parameters: { period: 42, multiplier: 3.5 } }];
  const normalized = initialIndicatorCatalog(server);
  assert.deepEqual(normalized, server);
  assert.notEqual(normalized[0].parameters, server[0].parameters);
  assert.deepEqual(initialIndicatorCatalog([]), []);
  assert.deepEqual(initialIndicatorCatalog([{ name: 'run_shell', tool: 'run_shell', parameters: {} }]), []);
});

test('default catalog copies cannot leak edits into later settings loads', () => {
  const first = initialIndicatorCatalog(null);
  first[0].parameters.period = 999;
  assert.equal(initialIndicatorCatalog(undefined)[0].parameters.period, 20);
});

test('selections sent to settings are canonical, deduplicated, and limited to supported names', () => {
  assert.deepEqual(normalizeInitialIndicators(['stochastic', 'bollinger', 'bollinger', 'rsi', 'run_shell']),
    ['bollinger', 'stochastic']);
  for (const invalid of [null, undefined, 42, 'bollinger', {}]) assert.deepEqual(normalizeInitialIndicators(invalid), []);
  assert.equal(sameInitialIndicators(['stochastic', 'bollinger'], ['bollinger', 'stochastic']), true);
  assert.equal(sameInitialIndicators(['bollinger'], []), false);
});

test('checkbox labels, purposes, and parameter badges are complete in both languages', async () => {
  const catalog = initialIndicatorCatalog(undefined);
  const chinese = /[\u3400-\u9fff]/;
  await setUiLocale('en-US');
  for (const item of catalog) {
    const copy = initialIndicatorCopy(item.name);
    assert.ok(copy.label.trim() && copy.purpose.trim());
    assert.equal(chinese.test(`${copy.label} ${copy.purpose}`), false, item.name);
    const badges = initialIndicatorParameterBadges(item.parameters);
    assert.equal(chinese.test(badges.join(' ')), false, item.name);
    for (const value of Object.values(item.parameters).filter(value => typeof value === 'number')) {
      assert.ok(badges.some(badge => badge.endsWith(` ${value}`)), `${item.name}: ${value}`);
    }
  }
  assert.deepEqual(initialIndicatorParameterBadges({ period: 42, multiplier: 3.5 }),
    ['Period 42', 'Multiplier 3.5']);
  await setUiLocale('zh-TW');
  assert.deepEqual(initialIndicatorParameterBadges({ lookback: 160, direction: 'auto' }),
    ['回看根數 160', '波段方向 自動']);
});
