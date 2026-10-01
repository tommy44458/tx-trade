import assert from 'node:assert/strict';
import { test } from 'node:test';
import { applyPendingAnalysisUpdate, createPositionAnalysisLookup, isLatestPositionAnalysis } from '../src/latestPositionAnalysis.ts';

const saved = (marketId = 'binance:perp:BTCUSDT', overrides = {}) => ({
  id: 'saved-position-analysis', status: 'completed',
  submitted_input: { kind: 'positions', market_id: marketId, timeframe: '4h' },
  report: { market_id: marketId, timeframe: '4h' }, ...overrides,
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test('only completed position reports for the exact pair and original supported timeframe can load', () => {
  const pair = 'binance:perp:BTCUSDT';
  assert.equal(isLatestPositionAnalysis(saved(), pair), true);
  for (const timeframe of ['1h', '4h', '12h', '1d']) {
    const report = saved(pair, { submitted_input: { kind: 'positions', market_id: pair, timeframe },
      report: { market_id: pair, timeframe } });
    assert.equal(isLatestPositionAnalysis(report, pair), true);
  }
  for (const record of [null, {}, saved('binance:perp:SOLUSDT'), saved(pair, { status: 'queued' }),
    saved(pair, { status: 'failed' }), saved(pair, { report: null }), saved(pair, { id: '' }),
    saved(pair, { submitted_input: { kind: 'market', market_id: pair, timeframe: '4h' } }),
    saved(pair, { report: { market_id: pair, timeframe: '1h' } }),
    saved(pair, { report: { market_id: 'binance:perp:SOLUSDT', timeframe: '4h' } }),
    saved(pair, { submitted_input: { kind: 'positions', market_id: pair, timeframe: '1m' },
      report: { market_id: pair, timeframe: '1m' } })]) {
    assert.equal(isLatestPositionAnalysis(record, pair), false);
  }
});

test('a delayed BTC lookup cannot replace the selected SOL report even if abort is ignored', async () => {
  const lookup = createPositionAnalysisLookup();
  const btc = deferred(), sol = deferred(), updates = [], signals = [];
  const first = lookup.read(signal => { signals.push(signal); return btc.promise; }, value => updates.push(value), assert.fail);
  const second = lookup.read(signal => { signals.push(signal); return sol.promise; }, value => updates.push(value), assert.fail);
  const solReport = saved('binance:perp:SOLUSDT');
  sol.resolve(solReport); await second;
  btc.resolve(saved()); await first;
  assert.deepEqual(updates, [solReport]);
  assert.equal(signals[0].aborted, true);
  assert.equal(signals[1].aborted, false);
});

test('starting a manual analysis or opening explicit history invalidates pending auto-load and timeframe callbacks', async () => {
  const lookup = createPositionAnalysisLookup(), response = deferred();
  let applied = 0, errors = 0;
  const pending = lookup.read(() => response.promise, () => { applied += 1; }, () => { errors += 1; });
  lookup.invalidate();
  response.resolve(saved()); await pending;
  assert.equal(applied, 0);
  assert.equal(errors, 0);
});

test('an empty selected pair stays empty when a previous pair reports a delayed error', async () => {
  const lookup = createPositionAnalysisLookup(), oldResponse = deferred();
  const updates = [], errors = [];
  const previous = lookup.read(() => oldResponse.promise, value => updates.push(value), error => errors.push(error));
  await lookup.read(async () => null, value => updates.push(value), error => errors.push(error));
  oldResponse.reject(new Error('Old pair request failed')); await previous;
  assert.deepEqual(updates, [null]);
  assert.deepEqual(errors, []);
});

test('a current read failure is retryable without creating or altering a saved report', async () => {
  const lookup = createPositionAnalysisLookup(), report = saved();
  const original = structuredClone(report), updates = [], errors = [];
  await lookup.read(async () => { throw new Error('Offline'); }, value => updates.push(value), error => errors.push(error.message));
  await lookup.read(async () => report, value => updates.push(value), error => errors.push(error.message));
  assert.deepEqual(errors, ['Offline']);
  assert.deepEqual(updates, [report]);
  assert.deepEqual(report, original);
});

test('a slower poll cannot downgrade a completed task or replace a different explicitly viewed report', () => {
  const running = { id: 'new-analysis', status: 'running' };
  const completed = { id: running.id, status: 'completed', report: { reason: 'Current result' } };
  assert.equal(applyPendingAnalysisUpdate(running, completed), completed);
  assert.equal(applyPendingAnalysisUpdate(completed, running), completed);
  assert.equal(applyPendingAnalysisUpdate(null, running), null);
  const historical = { id: 'older-picked-analysis', status: 'completed' };
  assert.equal(applyPendingAnalysisUpdate(historical, completed), historical);
  const failed = { id: running.id, status: 'failed' };
  assert.equal(applyPendingAnalysisUpdate(failed, running), failed);
});
