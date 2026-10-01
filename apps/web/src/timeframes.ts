import { uiText } from "./i18n/index.ts";
export const ANALYSIS_TIMEFRAMES = ["1h", "4h", "12h", "1d"] as const;
export type AnalysisTimeframe = (typeof ANALYSIS_TIMEFRAMES)[number];
export const TIMEFRAME_LADDER = ["1h", "4h", "12h", "1d", "3d", "1w", "1M"] as const;
export type MarketTimeframe = (typeof TIMEFRAME_LADDER)[number];

const CONTEXT_TIMEFRAMES: Record<AnalysisTimeframe, readonly MarketTimeframe[]> = {
  "1h": ["4h", "12h", "1d"],
  "4h": ["12h", "1d", "3d"],
  "12h": ["1d", "3d", "1w"],
  "1d": ["3d", "1w", "1M"],
};

export function isAnalysisTimeframe(value: string): value is AnalysisTimeframe {
  return (ANALYSIS_TIMEFRAMES as readonly string[]).includes(value);
}

export function contextTimeframes(primary: string): readonly MarketTimeframe[] {
  return isAnalysisTimeframe(primary) ? CONTEXT_TIMEFRAMES[primary] : [];
}

export function timeframeCode(value: string): string {
  return (TIMEFRAME_LADDER as readonly string[]).includes(value) ? value.toUpperCase() : value;
}

export function timeframeLabel(value: string): string {
  return ({ "1h": uiText("1 小時"), "4h": uiText("4 小時"), "12h": uiText("12 小時"), "1d": uiText("日線"), "3d": uiText("3 日線"), "1w": uiText("週線"), "1M": uiText("月線") } as Record<string, string>)[value] ?? value;
}

export function orderedTimeframes(primary: string, available: string[]): string[] {
  const preferred = [primary, ...contextTimeframes(primary)];
  const known = new Set(available);
  return [...new Set([...preferred.filter(value => known.has(value)),
    ...TIMEFRAME_LADDER.filter(value => known.has(value)), ...available])];
}

export function snapshotTimeframes(primary: string, declared: string[] | undefined, available: string[]): string[] {
  return declared?.length ? [...new Set([primary, ...declared])] : orderedTimeframes(primary, available);
}
