import { uiText } from "./i18n/index.ts";
import type { UiTextKey } from "./i18n/resources.ts";
import type { InitialIndicatorCatalogItem } from "./localSettings.ts";

export const INITIAL_INDICATOR_NAMES = [
  "bollinger", "fibonacci", "adx_dmi", "obv", "donchian", "keltner", "stochastic",
] as const;
export type InitialIndicatorName = typeof INITIAL_INDICATOR_NAMES[number];

const FALLBACK_CATALOG: InitialIndicatorCatalogItem[] = [
  { name: "bollinger", tool: "bollinger", parameters: { period: 20, multiplier: 2 } },
  { name: "fibonacci", tool: "fibonacci", parameters: { lookback: 160, width: 3, direction: "auto" } },
  { name: "adx_dmi", tool: "adx_dmi", parameters: { period: 14, adx_period: 14 } },
  { name: "obv", tool: "obv", parameters: { period: 20 } },
  { name: "donchian", tool: "donchian", parameters: { period: 20 } },
  { name: "keltner", tool: "keltner", parameters: { period: 20, atr_period: 14, multiplier: 2 } },
  { name: "stochastic", tool: "stochastic", parameters: { period: 14, smooth_k: 3, smooth_d: 3 } },
];

const COPY: Record<InitialIndicatorName, { label: UiTextKey; purpose: UiTextKey }> = {
  bollinger: { label: "布林通道", purpose: "比較價格相對波動區間的位置，不代表碰到邊界就會反轉。" },
  fibonacci: { label: "斐波那契回撤", purpose: "觀察已確認波段的回撤比例，輔助評估價格位置。" },
  adx_dmi: { label: "平均趨向指標 ADX / DMI", purpose: "比較趨勢強度與多空動能，不單靠 ADX 決定方向。" },
  obv: { label: "能量潮 OBV", purpose: "觀察價格與累積成交量是否同向。" },
  donchian: { label: "唐奇安通道", purpose: "比較價格與近期高低邊界，觀察突破或區間走勢。" },
  keltner: { label: "肯特納通道", purpose: "以均線與 ATR 比較波動範圍和價格延伸程度。" },
  stochastic: { label: "隨機指標", purpose: "觀察收盤價在近期高低區間的位置與動能變化。" },
};

const PARAMETER_LABELS: Record<string, UiTextKey> = {
  period: "期數", multiplier: "倍數", lookback: "回看根數", width: "轉折寬度",
  direction: "波段方向", adx_period: "ADX 平滑", atr_period: "ATR 期數",
  smooth_k: "%K 平滑", smooth_d: "%D 平滑",
};

export function initialIndicatorCatalog(value: unknown): InitialIndicatorCatalogItem[] {
  if (!Array.isArray(value)) return FALLBACK_CATALOG.map(item => ({ ...item, parameters: { ...item.parameters } }));
  return INITIAL_INDICATOR_NAMES.flatMap(name => {
    const item = value.find(candidate => candidate && typeof candidate === "object" && candidate.name === name);
    if (!item || typeof item.tool !== "string" || !item.parameters || typeof item.parameters !== "object"
        || Array.isArray(item.parameters)) return [];
    const parameters: Record<string, number | string> = {};
    for (const [key, parameter] of Object.entries(item.parameters)) {
      if (typeof parameter === "string" || typeof parameter === "number" && Number.isFinite(parameter)) parameters[key] = parameter;
    }
    return [{ name, tool: item.tool, parameters }];
  });
}

export function normalizeInitialIndicators(value: unknown): InitialIndicatorName[] {
  const names = new Set(Array.isArray(value) ? value : []);
  return INITIAL_INDICATOR_NAMES.filter(name => names.has(name));
}

export function sameInitialIndicators(first: unknown, second: unknown): boolean {
  return normalizeInitialIndicators(first).join(",") === normalizeInitialIndicators(second).join(",");
}

export function initialIndicatorCopy(name: string): { label: string; purpose: string } {
  const copy = COPY[name as InitialIndicatorName];
  return copy ? { label: uiText(copy.label), purpose: uiText(copy.purpose) } : { label: name, purpose: "" };
}

export function initialIndicatorParameterBadges(parameters: Record<string, number | string>): string[] {
  return Object.entries(parameters).map(([name, value]) => {
    const label = PARAMETER_LABELS[name];
    return `${label ? uiText(label) : name} ${value === "auto" ? uiText("自動") : value}`;
  });
}
