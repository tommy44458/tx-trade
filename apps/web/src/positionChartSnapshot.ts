import { uiText } from "./i18n/index.ts";
import type { ChartCandle } from "./candlestickData";
import type { AnalysisTimeframe } from "./timeframes";

export type PositionChartSnapshot = {
  analysis_id: string;
  status: "ready";
  market_id: string;
  timeframe: AnalysisTimeframe;
  as_of: string;
  quote: { price: string; observed_at: string };
  market_snapshot_sha256: string;
  chart_candles: ChartCandle[];
  closed_candle_count?: number;
  available_closed_candle_count?: number;
  forming_candle_status?: "available" | "unavailable";
  data_basis?: "stored_analysis_snapshot";
} | {
  analysis_id: string;
  status: "unavailable";
  reason: string;
  chart_candles: [];
};

type SnapshotReference = {
  marketId: string;
  timeframe: string;
  quotePrice: string;
  observedAt: string;
  analysisId?: string;
  snapshotHash?: string;
};

export function matchingPositionSnapshot(
  snapshot: PositionChartSnapshot | null | undefined,
  reference: SnapshotReference,
): boolean {
  if (!snapshot || snapshot.status !== "ready") return false;
  const expectedTime = Date.parse(reference.observedAt);
  const quotedTime = Date.parse(snapshot.quote.observed_at);
  return (
    snapshot.market_id === reference.marketId &&
    snapshot.timeframe === reference.timeframe &&
    Number.isFinite(expectedTime) &&
    quotedTime === expectedTime &&
    Date.parse(snapshot.as_of) === expectedTime &&
    Number.isFinite(Number(reference.quotePrice)) &&
    Number(reference.quotePrice) > 0 &&
    Number(snapshot.quote.price) === Number(reference.quotePrice) &&
    (!reference.analysisId || snapshot.analysis_id === reference.analysisId) &&
    (!reference.snapshotHash || snapshot.market_snapshot_sha256 === reference.snapshotHash) &&
    snapshot.chart_candles.length > 0
  );
}

export function positionSnapshotMessage(reason?: string): string {
  switch (reason) {
    case "SNAPSHOT_NOT_SAVED":
    case "REPORT_NOT_SAVED":
      return uiText("這份舊報告沒有保存 K 線快照；重新分析後即可查看對應圖表。");
    case "ANALYSIS_NOT_COMPLETED":
      return uiText("本次分析尚未完成，K 線快照將於報告完成後顯示。");
    case "SNAPSHOT_REPORT_MISMATCH":
    case "SNAPSHOT_INVALID":
      return uiText("本次保存的 K 線快照無法與報告對應，圖表暫時無法顯示。");
    default:
      return uiText("本次報告沒有可用的 K 線快照；重新分析後即可查看對應圖表。");
  }
}
