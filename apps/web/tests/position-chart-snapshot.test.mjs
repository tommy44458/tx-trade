import assert from 'node:assert/strict';
import { test } from 'node:test';
import { matchingPositionSnapshot, positionSnapshotMessage } from '../src/positionChartSnapshot.ts';

const reference = {
  analysisId: 'analysis-1', marketId: 'binance:perp:SOLUSDT', timeframe: '1h',
  quotePrice: '121.20', observedAt: '2026-10-01T09:30:00+08:00', snapshotHash: 'frozen-hash',
};
const snapshot = {
  analysis_id: 'analysis-1', status: 'ready', market_id: 'binance:perp:SOLUSDT',
  timeframe: '1h', as_of: '2026-10-01T01:30:00+00:00',
  quote: {price: '121.2', observed_at: '2026-10-01T01:30:00Z'},
  market_snapshot_sha256: 'frozen-hash',
  chart_candles: [{open_time:'2026-10-01T00:00:00Z',close_time:'2026-10-01T00:59:59.999Z',open:'120',high:'122',low:'119',close:'121',volume:'20',closed:true}],
};

test('frozen report chart accepts equivalent price and timezone representations', () => {
  assert.equal(matchingPositionSnapshot(snapshot, reference), true);
});

test('a different report, market, timeframe, quote, or as-of cannot reuse the snapshot', () => {
  for (const patch of [
    {analysis_id:'analysis-2'}, {market_id:'binance:perp:BTCUSDT'}, {timeframe:'4h'},
    {as_of:'2026-10-01T01:31:00Z'}, {market_snapshot_sha256:'new-hash'},
    {quote:{...snapshot.quote,price:'122'}},
    {quote:{...snapshot.quote,observed_at:'2026-10-01T01:31:00Z'}},
    {chart_candles:[]},
  ]) assert.equal(matchingPositionSnapshot({...snapshot,...patch},reference),false);
});

test('missing legacy snapshots never become current-market chart data', () => {
  assert.equal(matchingPositionSnapshot(null,reference),false);
  assert.equal(matchingPositionSnapshot({status:'unavailable',reason:'SNAPSHOT_NOT_SAVED',analysis_id:'analysis-1',chart_candles:[]},reference),false);
  assert.match(positionSnapshotMessage('SNAPSHOT_NOT_SAVED'),/舊報告沒有保存 K 線快照/);
  assert.match(positionSnapshotMessage('SNAPSHOT_REPORT_MISMATCH'),/無法與報告對應/);
});

test('12-hour and daily reports retain their own frozen chart interval', () => {
  for (const timeframe of ['12h', '1d']) {
    assert.equal(matchingPositionSnapshot({...snapshot, timeframe}, {...reference, timeframe}), true);
    assert.equal(matchingPositionSnapshot({...snapshot, timeframe}, reference), false);
  }
});
