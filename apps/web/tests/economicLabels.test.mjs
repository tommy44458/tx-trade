import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { economicMetricLabel } from '../src/economicLabels.ts';
import { setUiLocale } from '../src/i18n/index.ts';

afterEach(async () => { await setUiLocale('zh-TW'); });

test('official normalized metric names translate without changing release headlines', async () => {
  await setUiLocale('en-US');
  assert.equal(economicMetricLabel('CPI 年增率', 'cpi_yoy'), 'CPI year-over-year');
  assert.equal(economicMetricLabel('非農就業月增量', 'payroll_change'), 'Monthly nonfarm payroll change');
  assert.equal(economicMetricLabel('實質 GDP 年化季增率', 'real_gdp_annualized'), 'Real GDP annualized quarter-over-quarter');
  const sourceTitle = '消費者物價指數：2026 年 8 月官方公告';
  assert.equal(economicMetricLabel(sourceTitle, 'cpi_yoy'), sourceTitle);
  assert.equal(economicMetricLabel('自訂統計', 'unknown'), '自訂統計');
  assert.equal(economicMetricLabel('CPI 年增率', 'pce_yoy'), 'CPI 年增率');
});

test('explicit metric display locale is independent of the active UI locale', async () => {
  await setUiLocale('zh-TW');
  assert.equal(economicMetricLabel('核心 PCE 物價年增率', 'core_pce_yoy', 'en-US'), 'Core PCE inflation year-over-year');
  assert.equal(economicMetricLabel('失業率', 'unemployment_rate', 'zh-TW'), '失業率');
  assert.equal(economicMetricLabel(undefined), '');
});
