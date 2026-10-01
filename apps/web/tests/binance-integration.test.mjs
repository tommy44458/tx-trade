import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { setUiLocale } from '../src/i18n/index.ts';
import { SettingsRequestError, settingsRequest } from '../src/localSettings.ts';
import { binanceFailureMessage, binanceWritePermissionLabel, hasVerifiedReadOnlyPermissions } from '../src/binanceIntegration.ts';
import { importedPositionFacts, isManualPosition, liquidationSourceLabel, positionSourceLabel } from '../src/positionSources.ts';

afterEach(async () => { await setUiLocale('zh-TW'); });

const readTest = permissions => ({ readable: true, active: 0, active_symbols: [], unsupported: 0,
  permissions: { verified: true, read_only: true, reading: true, write_permissions: [], ...permissions } });

test('readable positions, including zero positions, do not prove read-only permissions', () => {
  assert.equal(hasVerifiedReadOnlyPermissions(readTest({})), true);
  for (const permissions of [
    { verified: false }, { verified: 'true' }, { read_only: null }, { read_only: false },
    { reading: null }, { reading: false }, { write_permissions: undefined },
    { write_permissions: ['enableFutures'] }, { write_permissions: ['enableFixApiTrade'] },
    { write_permissions: ['enablePortfolioMarginTrading'] },
  ]) assert.equal(hasVerifiedReadOnlyPermissions(readTest(permissions)), false);
});

test('all official extra permissions have bilingual labels without suggesting they are required', async () => {
  const permissions = ['enableWithdrawals', 'enableInternalTransfer', 'enableMargin', 'enableFutures',
    'permitsUniversalTransfer', 'enableVanillaOptions', 'enableFixApiTrade', 'enableSpotAndMarginTrading', 'enablePortfolioMarginTrading'];
  await setUiLocale('zh-TW');
  const chinese = permissions.map(binanceWritePermissionLabel);
  await setUiLocale('en-US');
  const english = permissions.map(binanceWritePermissionLabel);
  assert.ok(chinese.every((value, index) => value !== permissions[index]));
  assert.ok(english.every((value, index) => value !== permissions[index] && !/[\u3400-\u9fff]/.test(value)));
  assert.equal(binanceWritePermissionLabel('future_unknown_permission'), 'future_unknown_permission');
  assert.equal(binanceWritePermissionLabel('enableFixApiTrade'), 'FIX API trading');
});

test('only manual and legacy manual sources can change exchange facts or close a local position', async () => {
  for (const source of ['manual', null, undefined]) assert.equal(isManualPosition(source), true);
  for (const source of ['bingx', 'binance', 'unknown', '']) assert.equal(isManualPosition(source), false);
  await setUiLocale('en-US');
  assert.equal(positionSourceLabel('binance', 'perpetual'), 'Binance USDT perpetual');
  assert.equal(positionSourceLabel('bingx', 'standard'), 'BingX Standard Futures');
  assert.equal(positionSourceLabel('manual'), 'Manual record');
  assert.equal(liquidationSourceLabel('binance'), 'Binance liquidation price');
  await setUiLocale('zh-TW');
  assert.equal(positionSourceLabel('binance'), 'Binance USDT 永續');
});

test('an imported edit submits authoritative facts with exact numeric strings and no credential or mutable state', () => {
  const position = Object.freeze({ market_id: 'ADA-USDT', side: 'short', leverage: 75, margin_mode: 'cross',
    entry_price: '0.00401234000', quantity: '12000.500', exchange_liquidation_price: '0.00700999000',
    entry_time: '2026-10-01T12:34:00+00:00', version: 3, source: 'binance', notes: 'old notes', api_key: 'never-project' });
  assert.deepEqual(importedPositionFacts(position), {
    market_id: 'ADA-USDT', side: 'short', leverage: 75, margin_mode: 'cross',
    entry_price: '0.00401234000', quantity: '12000.500', exchange_liquidation_price: '0.00700999000',
    entry_time: '2026-10-01T12:34:00+00:00',
  });
  const noOptional = importedPositionFacts({ ...position, entry_time: undefined, exchange_liquidation_price: undefined });
  assert.equal(noOptional.entry_time, null);
  assert.equal(noOptional.exchange_liquidation_price, null);
});

test('request errors retain only bounded safe metadata, not raw upstream response properties', async () => {
  const originalFetch = globalThis.fetch;
  const detail = { code: 'RATE_LIMITED', message: 'Safe fixture message', exchange_code: -1003, retry_after: 60,
    headers: { APIKey: 'fixture-secret' }, url: 'private-url', response_body: 'never-retain' };
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail }) });
  try {
    await assert.rejects(settingsRequest('/positions/binance/sync', { method: 'POST' }), error => {
      assert.ok(error instanceof SettingsRequestError);
      assert.equal(error.message, 'Safe fixture message');
      assert.equal(error.code, 'RATE_LIMITED');
      assert.equal(error.exchangeCode, -1003);
      assert.equal(error.retryAfter, 60);
      assert.equal('headers' in error, false);
      assert.equal(JSON.stringify(error).includes('fixture-secret'), false);
      return true;
    });
    const invalid = new SettingsRequestError('safe', { code: 'secret-url?value=key', exchange_code: NaN, retry_after: -1 });
    assert.equal(invalid.code, null); assert.equal(invalid.exchangeCode, null); assert.equal(invalid.retryAfter, null);
  } finally { globalThis.fetch = originalFetch; }
});

test('known failures translate in both languages and never describe a failed account-mode check as no holdings', async () => {
  for (const locale of ['zh-TW', 'en-US']) {
    await setUiLocale(locale);
    for (const code of ['UNSUPPORTED_ACCOUNT_MODE', 'ACCOUNT_MODE_UNVERIFIED', 'BINANCE_SYNC_SUPERSEDED']) {
      const text = binanceFailureMessage(new SettingsRequestError('fallback English', { code }));
      assert.ok(text.includes(code)); assert.equal(text.includes('fallback English'), false);
      assert.equal(/no positions|沒有持倉/.test(text), false);
      if (locale === 'en-US') assert.equal(/[\u3400-\u9fff]/.test(text), false);
    }
    const text = binanceFailureMessage(new SettingsRequestError('fallback', { code: 'RATE_LIMITED', exchange_code: -1003, retry_after: 30 }));
    assert.ok(text.includes('RATE_LIMITED / -1003'));
    assert.ok(text.includes('30'));
  }
});
