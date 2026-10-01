import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  chartPrice,
  chartRefreshOffset,
  chartZones,
  currentChartPrice,
  isInvalidatedZone,
  nearestChartZones,
  normalizeCandles,
} from '../src/candlestickData.ts';

const candle = (time, close = '101') => ({
  open_time: time,
  close_time: time + 3600,
  open: '100', high: '103', low: '98', close, volume: '1200', closed: true,
});

test('normalize candles orders and deduplicates data and excludes invalid OHLC', () => {
  const candles = normalizeCandles([
    candle(3600), candle(0), candle(3600, '102'),
    { ...candle(7200), low: '110' },
    { ...candle(10800), open: 'NaN' },
  ]);
  assert.deepEqual(candles.map(({ time, close }) => [time, close]), [[0, 101], [3600, 102]]);
  assert.equal(candles[1].volume, 1200);
  assert.equal(candles[1].closed, true);
});

test('ISO and millisecond exchange timestamps resolve to the same UTC candle', () => {
  const iso = '2026-10-01T01:00:00+08:00';
  const epochMs = Date.parse(iso);
  const parsed = normalizeCandles([
    { ...candle(0), open_time: iso },
    { ...candle(0), open_time: epochMs, close: '102' },
  ]);
  assert.equal(parsed.length, 1);
  assert.equal(parsed[0].time, epochMs / 1000);
  assert.equal(parsed[0].close, 102);
});

test('live quote wins over a closed candle and ignores unavailable quote values', () => {
  const data = normalizeCandles([candle(0)]);
  assert.equal(currentChartPrice('102.5', data), 102.5);
  assert.equal(currentChartPrice('NaN', data), 101);
  assert.equal(currentChartPrice(undefined, []), undefined);
});

test('the entire original zone including both edges remains visible across its center', () => {
  const level = { kind: 'resistance', low: '100', high: '103', price_relation: 'above' };
  for (const price of [100, 101, 102, 103]) {
    const [zone] = chartZones([level], price);
    assert.equal(zone.inside, true);
    assert.equal(zone.kind, 'resistance');
    assert.deepEqual([zone.low, zone.high], [100, 103]);
  }
  assert.equal(chartZones([level], 103.01)[0].inside, false);
  assert.equal(chartZones([level], 99.99)[0].inside, false);
});

test('invalidated original zones survive rendering selection and have a distinct state', () => {
  const [zone] = chartZones([
    { kind: 'resistance', low: 100, high: 103, zone_state: 'invalidated' },
  ], 102);
  assert.equal(isInvalidatedZone(zone), true);
  assert.equal(zone.inside, true);
  assert.deepEqual(nearestChartZones([zone], 102), [zone]);
});

test('proximity selection retains every overlapping live zone without crowding distant zones', () => {
  const zones = chartZones([
    ...Array.from({ length: 5 }, (_, id) => ({ id: String(id), kind: 'resistance', low: 100 + id, high: 110 })),
    { id: 'far', kind: 'resistance', low: 200, high: 210 },
    { id: 'original', kind: 'resistance', low: 101, high: 105, zone_state: 'invalidated' },
  ], 105);
  const selected = nearestChartZones(zones, 105);
  assert.equal(selected.length, 6);
  assert.equal(selected.some((zone) => zone.id === 'far'), false);
  assert.equal(selected.every((zone) => zone.inside), true);
});

test('refresh offsets preserve the visible timestamps when history is prepended or rolls forward', () => {
  const previous = normalizeCandles([candle(3600), candle(7200), candle(10800)]);
  assert.equal(chartRefreshOffset(previous, normalizeCandles([candle(0), ...previous.map((item) => candle(item.time))])), 1);
  assert.equal(chartRefreshOffset(previous, normalizeCandles([candle(7200), candle(10800), candle(14400)])), -1);
  assert.equal(chartRefreshOffset(previous, normalizeCandles([candle(3600), candle(7200), candle(10800, '102')])), 0);
});

test('low-price perpetual contracts retain meaningful price precision', () => {
  assert.equal(chartPrice(0.00001234), '0.00001234');
  assert.equal(chartPrice(85_277.4), '85,277.4');
});
