import assert from 'node:assert/strict';
import test from 'node:test';
import { fullWidthPunctuation, localizeReportText, spaceBetweenScripts } from '../src/zhPunctuation.ts';

test('a comma next to Chinese becomes full-width, with the following space dropped', () => {
  assert.equal(fullWidthPunctuation('站上阻力,動能轉強'), '站上阻力，動能轉強');
  assert.equal(fullWidthPunctuation('RSI 72, 偏高'), 'RSI 72，偏高');
  assert.equal(fullWidthPunctuation('上漲,8500 附近'), '上漲，8500 附近');
  assert.equal(fullWidthPunctuation('進場 85,400,止損 84,650'), '進場 85,400，止損 84,650');
  assert.equal(fullWidthPunctuation('（4H）,接著'), '（4H），接著');
});

test('a semicolon next to Chinese becomes full-width too, by the same rules', () => {
  assert.equal(fullWidthPunctuation('守住支撐;跌破就出場'), '守住支撐；跌破就出場');
  assert.equal(fullWidthPunctuation('RSI 72; 偏高,先觀望'), 'RSI 72；偏高，先觀望');
  assert.equal(fullWidthPunctuation('Hold; then trim'), 'Hold; then trim');
  assert.equal(fullWidthPunctuation('比例 1;2'), '比例 1;2');
});

test('thousands separators and commas between non-Chinese text stay as written', () => {
  assert.equal(fullWidthPunctuation('8,500'), '8,500');
  assert.equal(fullWidthPunctuation('區間 82,979.02–83,200.08'), '區間 82,979.02–83,200.08');
  assert.equal(fullWidthPunctuation('EMA20, EMA50 與 RSI'), 'EMA20, EMA50 與 RSI');
  assert.equal(fullWidthPunctuation('BTC,ETH'), 'BTC,ETH');
  assert.equal(fullWidthPunctuation('Price holds, so wait.'), 'Price holds, so wait.');
  assert.equal(fullWidthPunctuation('沒有逗號'), '沒有逗號');
});

test('only Traditional Chinese reports change, and every string in them is covered', () => {
  const report = { reasoning: { market: '偏多,但過熱;先等', levels: ['阻力 85,011,已穿越'] },
    entry_decision: { entry_price: '85400', reason: 'Holds, wait' }, score: 3, missing: null };
  const shown = localizeReportText(report, 'zh-TW');
  assert.equal(shown.reasoning.market, '偏多，但過熱；先等');
  assert.deepEqual(shown.reasoning.levels, ['阻力 85,011，已穿越']);
  assert.equal(shown.entry_decision.reason, 'Holds, wait');
  assert.equal(shown.entry_decision.entry_price, '85400');
  assert.equal(shown.score, 3);
  assert.equal(shown.missing, null);
  assert.equal(report.reasoning.market, '偏多,但過熱;先等');
  assert.equal(localizeReportText(report, 'en-US'), report);
});

test('Chinese next to English or numbers gets one space, punctuation none', () => {
  assert.equal(spaceBetweenScripts('4H/12H/1D結構仍為rising'), '4H/12H/1D 結構仍為 rising');
  assert.equal(spaceBetweenScripts('站上85,067阻力'), '站上 85,067 阻力');
  assert.equal(spaceBetweenScripts('RSI72偏高，EMA20向上'), 'RSI72 偏高，EMA20 向上');
  assert.equal(spaceBetweenScripts('上漲5%後回落'), '上漲 5% 後回落');
  assert.equal(spaceBetweenScripts('單日漲+2%'), '單日漲 +2%');
  // Already spaced, or next to Chinese punctuation: unchanged.
  assert.equal(spaceBetweenScripts('結構仍為 rising'), '結構仍為 rising');
  assert.equal(spaceBetweenScripts('（4H）「BTC」，ETH。'), '（4H）「BTC」，ETH。');
  assert.equal(spaceBetweenScripts('Price holds above 85,000.'), 'Price holds above 85,000.');
});

test('reports get both: full-width marks first, then spaced scripts', () => {
  const shown = localizeReportText({ reasoning: { market: '站上阻力85,067,接著4H與1D同步偏多' } }, 'zh-TW');
  assert.equal(shown.reasoning.market, '站上阻力 85,067，接著 4H 與 1D 同步偏多');
  assert.equal(localizeReportText({ text: '為rising' }, 'en-US').text, '為rising');
});

