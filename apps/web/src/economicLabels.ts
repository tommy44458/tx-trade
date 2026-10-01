import { uiText, type UiLocale, uiLocale } from "./i18n/index.ts";
import type { UiTextKey } from "./i18n/resources.ts";

// These are application-owned normalized statistic names, not release headlines.
const metricLabels: Readonly<Record<string, UiTextKey>> = {
  cpi_mom: "CPI 月增率",
  cpi_yoy: "CPI 年增率",
  core_cpi_yoy: "核心 CPI 年增率",
  payroll_change: "非農就業月增量",
  unemployment_rate: "失業率",
  pce_yoy: "PCE 物價年增率",
  core_pce_yoy: "核心 PCE 物價年增率",
  real_gdp_annualized: "實質 GDP 年化季增率",
};

export function economicMetricLabel(label: string | undefined, metric?: string, locale: UiLocale = uiLocale()): string {
  if (!label || locale === "zh-TW") return label ?? "";
  const key = metric ? metricLabels[metric] : Object.values(metricLabels).find(value => value === label);
  // Preserve custom labels and all official titles rather than inferring a translation.
  return key && label === key ? uiText(key, { lng: locale }) : label;
}
