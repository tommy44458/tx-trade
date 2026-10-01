import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { discussionLiveMarketView, formatDiscussionLivePrice } from '../src/discussionLiveMarket.ts';
import { setUiLocale } from '../src/i18n/index.ts';

afterEach(async () => { await setUiLocale('zh-TW'); });

const evidence = overrides => ({
  version: 'discussion_live_market_v1', status: 'available',
  market_id: 'binance:perp:BTCUSDT', timeframe: '1h', source: 'binance_usdt_perpetual',
  requested_at: '2026-09-30T13:16:00+00:00', observed_at: '2026-09-30T13:16:02+00:00',
  quote: { price: '85277.40000000', observed_at: '2026-09-30T13:16:01+00:00', tick_size: '0.10' },
  forming_candle: null, recent_closed_candles: [],
  comparison: { analysis_price: '85000', change_since_analysis_pct: '0.3263529' },
  errors: {}, not_refreshed: ['support_resistance', 'indicators'], ...overrides,
});

test('live quote formatting preserves exact price digits and contract tick precision', () => {
  assert.equal(formatDiscussionLivePrice('85277.40000000', '0.10'), '85,277.4');
  assert.equal(formatDiscussionLivePrice('0.00000123456789', '0.00000001'), '0.00000123456789');
  assert.equal(formatDiscussionLivePrice('0.00001000', '0.00000001'), '0.00001000');
  assert.equal(formatDiscussionLivePrice('1.23456789e-7', '1e-8'), '0.000000123456789');
  assert.equal(formatDiscussionLivePrice('9007199254740993.0123456789', '0.01'), '9,007,199,254,740,993.0123456789');
  assert.equal(formatDiscussionLivePrice('00012.5000', '0.0100'), '12.50');
  for (const price of [null, undefined, 10, '', '0', '-1', 'NaN', 'Infinity', '1e1000', '0x10']) {
    assert.equal(formatDiscussionLivePrice(price), null, String(price));
  }
});

test('a reply shows its own historical quote and observation time rather than the analysis price', () => {
  const original = evidence();
  const saved = JSON.stringify(original);
  Object.freeze(original.quote); Object.freeze(original);
  const view = discussionLiveMarketView(original, 'binance:perp:BTCUSDT');
  assert.equal(view.price, '85,277.4');
  assert.equal(view.observedAt, original.quote.observed_at);
  assert.equal(view.timeBasis, 'quote');
  assert.equal(view.pair, 'BTC/USDT');
  assert.equal(view.status, 'available');
  assert.equal(view.note, null);
  assert.equal(JSON.stringify(original), saved);
});

test('partial data keeps a valid current quote while unavailable data cannot reuse old prices', () => {
  assert.equal(discussionLiveMarketView(evidence({ status: 'partial', errors: { candles: 'UPSTREAM_UNAVAILABLE' } })).price, '85,277.4');
  for (const overrides of [
    { status: 'unavailable' }, { quote: null },
    { quote: { price: '85277.4', observed_at: 'invalid' } },
    { quote: { price: '0', observed_at: '2026-09-30T13:16:01Z' } },
  ]) {
    const view = discussionLiveMarketView(evidence({
      recent_closed_candles: [{ close: '85259.8' }], ...overrides,
    }));
    assert.equal(view.price, null);
    assert.equal(view.observedAt, '2026-09-30T13:16:00+00:00');
    assert.equal(view.timeBasis, 'request');
    assert.ok(view.note);
  }
});

test('a different frozen report pair hides a mismatched quote instead of relabeling it', () => {
  const view = discussionLiveMarketView(evidence(), 'binance:perp:SOLUSDT');
  assert.equal(view.pair, 'SOL/USDT');
  assert.equal(view.price, null);
  assert.equal(view.status, 'unavailable');
});

test('Unicode asset names accepted by the market catalog render as the exact price pair', () => {
  for (const asset of ['币安人生', '1000_中文²', 'Éclair']) {
    const marketId = `binance:perp:${asset}USDT`;
    const view = discussionLiveMarketView(evidence({ market_id: marketId }), marketId);
    assert.equal(view.pair, `${asset}/USDT`);
    assert.equal(view.price, '85,277.4');
    assert.equal(view.status, 'available');
  }
});

test('legacy and macro replies without live evidence remain unchanged', () => {
  assert.equal(discussionLiveMarketView(undefined), null);
  assert.equal(discussionLiveMarketView(null), null);
  assert.equal(discussionLiveMarketView({ version: 'old' }), null);
  const view = discussionLiveMarketView(evidence({ requested_at: 'invalid', quote: null }));
  assert.equal(view.observedAt, null);
  assert.equal(view.price, null);
});

test('live evidence language changes only labels and retains exact pair, month, price, and timestamp', async () => {
  const original = evidence({ timeframe: '1M', status: 'partial', market_id: 'binance:perp:1000SHIBUSDT',
    quote: { price: '0.001234567', observed_at: '2026-09-30T13:16:01Z', tick_size: '0.000001' } });
  await setUiLocale('zh-TW');
  const chinese = discussionLiveMarketView(original);
  await setUiLocale('en-US');
  const english = discussionLiveMarketView(original);
  assert.equal(english.pair, '1000SHIB/USDT');
  assert.equal(english.timeframe, '1M');
  assert.equal(english.price, '0.001234567');
  assert.equal(english.price, chinese.price);
  assert.equal(english.observedAt, chinese.observedAt);
  assert.equal(english.source, 'Binance USDT perpetual');
  assert.equal(english.note, 'Some market data could not be obtained.');
  assert.notEqual(english.note, chinese.note);
  assert.equal(discussionLiveMarketView(evidence({ quote: null })).note,
    'No current quote was obtained. The original analysis price is not a live quote.');
});
