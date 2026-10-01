import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ANALYSIS_TIMEFRAMES, contextTimeframes, isAnalysisTimeframe, orderedTimeframes, snapshotTimeframes, timeframeCode, timeframeLabel } from '../src/timeframes.ts';

test('each selectable analysis timeframe has exactly the next three higher frames', () => {
  assert.deepEqual(ANALYSIS_TIMEFRAMES, ['1h', '4h', '12h', '1d']);
  assert.deepEqual(contextTimeframes('1h'), ['4h', '12h', '1d']);
  assert.deepEqual(contextTimeframes('4h'), ['12h', '1d', '3d']);
  assert.deepEqual(contextTimeframes('12h'), ['1d', '3d', '1w']);
  assert.deepEqual(contextTimeframes('1d'), ['3d', '1w', '1M']);
});

test('monthly market context is not a selectable primary frame or a minute interval', () => {
  assert.equal(isAnalysisTimeframe('12h'), true);
  assert.equal(isAnalysisTimeframe('1d'), true);
  for (const value of ['1m', '1M', '3d', '1w', 'invalid']) assert.equal(isAnalysisTimeframe(value), false);
  assert.equal(timeframeLabel('1M'), '月線');
  assert.equal(timeframeCode('1M'), '1M');
  assert.equal(timeframeCode('1m'), '1m');
  assert.notEqual(timeframeLabel('1m'), '月線');
  assert.deepEqual(contextTimeframes('1m'), []);
});

test('snapshots retain their own timeframe plan and missing context columns', () => {
  assert.deepEqual(snapshotTimeframes('1d', ['1d', '3d', '1w', '1M'], ['1d', '3d']), ['1d', '3d', '1w', '1M']);
  assert.deepEqual(snapshotTimeframes('4h', ['4h', '12h', '1d', '3d'], ['1h', '4h', '12h', '1d']), ['4h', '12h', '1d', '3d']);
  assert.deepEqual(orderedTimeframes('1d', ['1M', '1w', '1d', '3d']), ['1d', '3d', '1w', '1M']);
});

test('legacy snapshots use their saved frames without inventing absent higher data', () => {
  assert.deepEqual(snapshotTimeframes('1h', undefined, ['1h', '4h', '12h', '1d']), ['1h', '4h', '12h', '1d']);
  assert.deepEqual(snapshotTimeframes('4h', undefined, ['1h', '4h']), ['4h', '1h']);
  assert.equal(timeframeLabel('12h'), '12 小時');
  assert.equal(timeframeLabel('1d'), '日線');
});
