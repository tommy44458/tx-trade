import assert from 'node:assert/strict';
import test from 'node:test';
import { fullWidthCommas, localizeReportText } from '../src/zhPunctuation.ts';

test('a comma next to Chinese becomes full-width, with the following space dropped', () => {
  assert.equal(fullWidthCommas('站上阻力,動能轉強'), '站上阻力，動能轉強');
  assert.equal(fullWidthCommas('RSI 72, 偏高'), 'RSI 72，偏高');
  assert.equal(fullWidthCommas('上漲,8500 附近'), '上漲，8500 附近');
  assert.equal(fullWidthCommas('進場 85,400,止損 84,650'), '進場 85,400，止損 84,650');
  assert.equal(fullWidthCommas('（4H）,接著'), '（4H），接著');
});

test('thousands separators and commas between non-Chinese text stay as written', () => {
  assert.equal(fullWidthCommas('8,500'), '8,500');
  assert.equal(fullWidthCommas('區間 82,979.02–83,200.08'), '區間 82,979.02–83,200.08');
  assert.equal(fullWidthCommas('EMA20, EMA50 與 RSI'), 'EMA20, EMA50 與 RSI');
  assert.equal(fullWidthCommas('BTC,ETH'), 'BTC,ETH');
  assert.equal(fullWidthCommas('Price holds, so wait.'), 'Price holds, so wait.');
  assert.equal(fullWidthCommas('沒有逗號'), '沒有逗號');
});

test('only Traditional Chinese reports change, and every string in them is covered', () => {
  const report = { reasoning: { market: '偏多,但過熱', levels: ['阻力 85,011,已穿越'] },
    entry_decision: { entry_price: '85400', reason: 'Holds, wait' }, score: 3, missing: null };
  const shown = localizeReportText(report, 'zh-TW');
  assert.equal(shown.reasoning.market, '偏多，但過熱');
  assert.deepEqual(shown.reasoning.levels, ['阻力 85,011，已穿越']);
  assert.equal(shown.entry_decision.reason, 'Holds, wait');
  assert.equal(shown.entry_decision.entry_price, '85400');
  assert.equal(shown.score, 3);
  assert.equal(shown.missing, null);
  assert.equal(report.reasoning.market, '偏多,但過熱');
  assert.equal(localizeReportText(report, 'en-US'), report);
});
