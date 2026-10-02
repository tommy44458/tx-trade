import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { indicatorLines, reportIndicators, swingMarkers } from '../src/chartIndicators.ts';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/indicator-parity.json', import.meta.url), 'utf8'));
const candles = fixture.rows.map(([open, close, o, h, l, c, v]) => ({
  time: Date.parse(open) / 1000, closeTime: Date.parse(close) / 1000,
  open: Number(o), high: Number(h), low: Number(l), close: Number(c), volume: Number(v),
}));
const last = (key, params, id) => indicatorLines({ key, params, pane: false, defaultOn: true }, candles)
  .find((line) => line.id === id).points.at(-1).value;
const same = (actual, expected, tolerance = 1e-9) =>
  assert.ok(Math.abs(actual - Number(expected)) <= tolerance * Math.max(1, Math.abs(Number(expected))),
    `${actual} differs from backend ${expected}`);

test('chart lines reproduce the backend indicator values on the same closed candles', () => {
  const e = fixture.expected;
  same(last('ema', { fast: 20, slow: 50 }, 'ema20'), e.ema20);
  same(last('ema', { fast: 20, slow: 50 }, 'ema50'), e.ema50);
  same(last('rsi', { period: 14 }, 'rsi'), e.rsi, 1e-4); // The backend rounds RSI to 0.01.
  same(last('macd', { fast: 12, slow: 26, signal: 9 }, 'macd'), e.macd);
  same(last('macd', { fast: 12, slow: 26, signal: 9 }, 'signal'), e.macd_signal);
  same(last('atr', { period: 14 }, 'atr'), e.atr);
  same(last('vwap', { period: 20 }, 'vwap'), e.vwap);
  same(last('bollinger', { period: 20, multiplier: 2 }, 'upper'), e.bollinger_upper);
  same(last('bollinger', { period: 20, multiplier: 2 }, 'lower'), e.bollinger_lower);
  same(last('keltner', { period: 20, atr_period: 14, multiplier: 2 }, 'upper'), e.keltner_upper);
  same(last('keltner', { period: 20, atr_period: 14, multiplier: 2 }, 'lower'), e.keltner_lower);
  same(last('donchian', { period: 20 }, 'upper'), e.donchian_upper);
  same(last('donchian', { period: 20 }, 'lower'), e.donchian_lower);
  same(last('obv', { period: 20 }, 'obv'), e.obv);
  same(last('adx', { period: 14, adx_period: 14 }, 'adx'), e.adx);
  same(last('adx', { period: 14, adx_period: 14 }, 'plus'), e.plus_di);
  same(last('adx', { period: 14, adx_period: 14 }, 'minus'), e.minus_di);
  same(last('stochastic', { period: 14, smooth_k: 3, smooth_d: 3 }, 'k'), e.stochastic_k);
  same(last('stochastic', { period: 14, smooth_k: 3, smooth_d: 3 }, 'd'), e.stochastic_d);
});

test('warmup candles are left blank instead of drawing invented values', () => {
  const [ema20] = indicatorLines({ key: 'ema', params: { fast: 20, slow: 50 }, pane: false, defaultOn: true }, candles);
  assert.equal(ema20.points[0].time, candles[19].time);
  const [rsi] = indicatorLines({ key: 'rsi', params: { period: 14 }, pane: true, defaultOn: true }, candles);
  assert.equal(rsi.points[0].time, candles[14].time);
  assert.deepEqual(indicatorLines({ key: 'bollinger', params: { period: 20, multiplier: 2 }, pane: false, defaultOn: true },
    candles.slice(0, 10)).map((line) => line.points.length), [0, 0, 0]);
});

test('swing markers mark only confirmed local highs and lows', () => {
  const markers = swingMarkers(candles, 3);
  assert.ok(markers.length > 0);
  for (const marker of markers) {
    const index = candles.findIndex((candle) => candle.time === marker.time);
    assert.ok(index >= 3 && index < candles.length - 3);
    const window = candles.slice(index - 3, index + 4);
    if (marker.kind === 'high') assert.equal(marker.price, Math.max(...window.map((candle) => candle.high)));
    else assert.equal(marker.price, Math.min(...window.map((candle) => candle.low)));
  }
});

const report = (overrides = {}) => ({
  timeframe: '1h',
  technical_snapshot: {
    timeframes: { '1h': { indicators: {
      trend_ema: { ema20: '1' }, rsi: { period: 14 }, macd: {}, volatility_atr: { period: 14 },
      rolling_vwap: { period: 20 }, swing_points: { width: 3 }, volume_signal: {},
      bollinger: { period: 20 }, obv: { period: 20 },
      fibonacci: { retracements: { '0.382': { price: '101.5', status: 'available' },
        '0.618': { price: '99.25', status: 'available' }, '0.786': { status: 'unavailable' } } },
    } } },
    initial_indicator_selection: { names: ['bollinger', 'fibonacci', 'obv'],
      parameters: { bollinger: { period: 20, multiplier: 2.5 }, obv: { period: 20 }, fibonacci: { lookback: 160 } } },
  },
  tool_trace: [], ...overrides,
});

test('the chart lists exactly the indicators the report computed, with its frozen parameters', () => {
  const specs = reportIndicators(report());
  assert.deepEqual(specs.map((spec) => spec.key),
    ['ema', 'vwap', 'bollinger', 'fibonacci', 'swing', 'rsi', 'macd', 'atr', 'obv']);
  const byKey = Object.fromEntries(specs.map((spec) => [spec.key, spec]));
  assert.equal(byKey.bollinger.params.multiplier, 2.5);
  assert.deepEqual(byKey.fibonacci.fibLevels, [{ ratio: '0.382', price: 101.5 }, { ratio: '0.618', price: 99.25 }]);
  // Selected optional indicators and the EMA trend start visible; the rest start off.
  assert.deepEqual(specs.filter((spec) => spec.defaultOn).map((spec) => spec.key), ['ema', 'bollinger', 'fibonacci', 'obv']);
  assert.deepEqual(specs.filter((spec) => spec.pane).map((spec) => spec.key), ['rsi', 'macd', 'atr', 'obv']);
});

test('optional tools the model requested itself are included; unavailable or other-timeframe ones are not', () => {
  const specs = reportIndicators(report({ tool_trace: [
    { tool: 'adx_dmi', result: { timeframe: '1h', status: 'available', parameters: { period: 21, adx_period: 14 } } },
    { tool: 'keltner', result: { timeframe: '4h', status: 'available' } },
    { tool: 'donchian', result: { timeframe: '1h', status: 'unavailable' } },
  ] }));
  const adx = specs.find((spec) => spec.key === 'adx');
  assert.equal(adx.params.period, 21);
  assert.equal(adx.defaultOn, false);
  assert.ok(!specs.some((spec) => spec.key === 'keltner' || spec.key === 'donchian'));
});

test('reports without a technical snapshot for their timeframe draw no indicators', () => {
  assert.deepEqual(reportIndicators(null), []);
  assert.deepEqual(reportIndicators({ timeframe: '4h', technical_snapshot: report().technical_snapshot }), []);
  const unavailable = report();
  unavailable.technical_snapshot.timeframes['1h'].indicators.rsi = { status: 'unavailable' };
  assert.ok(!reportIndicators(unavailable).some((spec) => spec.key === 'rsi'));
});
