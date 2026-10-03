import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assetForMarket,
  explorerUrl,
  formatAmount,
  formatUsd,
  lastSynced,
  seriesScale,
  shortAddress,
} from '../src/smartMoney.ts';

test('a market names its coin without the quote or the 1000x contract prefix', () => {
  assert.equal(assetForMarket('binance:perp:BTCUSDT'), 'BTC');
  assert.equal(assetForMarket('binance:perp:1000PEPEUSDT'), 'PEPE');
  assert.equal(assetForMarket('binance:perp:1000000MOGUSDT'), 'MOG');
  assert.equal(assetForMarket('binance:perp:ETHUSDC'), 'ETH');
  // A coin whose name starts with digits keeps them.
  assert.equal(assetForMarket('binance:perp:1INCHUSDT'), '1INCH');
});

test('large dollar amounts are compact, small ones exact, and signs only when asked', () => {
  assert.equal(formatUsd(3_250_000, 'en-US'), '$3.3M');
  assert.equal(formatUsd(-14_400_000, 'en-US', true), '-$14.4M');
  assert.equal(formatUsd(14_400_000, 'en-US', true), '+$14.4M');
  assert.equal(formatUsd(0, 'en-US', true), '$0');
  assert.equal(formatUsd(950, 'en-US'), '$950');
  assert.match(formatUsd(3_250_000, 'zh-TW'), /^US\$325萬$/);
});

test('coin amounts keep enough precision for their size', () => {
  assert.equal(formatAmount(732.42, 'en-US'), '732');
  assert.equal(formatAmount(12.345, 'en-US'), '12.35');
  assert.equal(formatAmount(0.78339, 'en-US'), '0.7834');
  assert.equal(formatAmount(25_642_066_780, 'en-US'), '25.6B');
});

test('addresses are shortened to recognisable ends, and transactions link to the right explorer', () => {
  assert.equal(shortAddress('0x28c6c06298d514db089934071355e5743bf21d60'), '0x28c6…1d60');
  assert.equal(shortAddress('34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo'), '34xp4v…wseo');
  assert.equal(shortAddress('bc1short'), 'bc1short');
  assert.equal(shortAddress(null), '—');
  assert.equal(explorerUrl({ chain: 'btc', tx: 'ab' }), 'https://mempool.space/tx/ab');
  assert.equal(explorerUrl({ chain: 'eth', tx: '0xab' }), 'https://etherscan.io/tx/0xab');
});

test('freshness comes from the latest chain run, and bars share one scale', () => {
  assert.equal(lastSynced({}), null);
  assert.equal(lastSynced({ btc: { height: 1, updated_at: 5 }, eth: { height: 2, updated_at: 9 } }), 9);
  const point = (inflow_usd, outflow_usd) => ({ t: 0, inflow: 0, outflow: 0, inflow_usd, outflow_usd });
  assert.equal(seriesScale([point(10, 40), point(25, 0)]), 40);
  assert.equal(seriesScale([point(0, 0)]), 1);
});
