import { uiText, uiLocale } from "./i18n/index.ts";
import "./PositionLevels.css";
import { levelLocationLabel, type LevelLocation } from "./levelLocation";
import CandlestickChart from "./CandlestickChart";
import { timeframeLabel } from "./timeframes";
import {
  matchingPositionSnapshot,
  positionSnapshotMessage,
  type PositionChartSnapshot,
} from "./positionChartSnapshot";

export type { PositionChartSnapshot } from "./positionChartSnapshot";

type Level = LevelLocation & {
  id?: string;
  kind: string;
  low: string;
  high: string;
  confirmed_at: string;
  pivot_count?: number;
  independent_touch_count?: number;
  zone_state?: string;
};

const price = (value: string) => {
  const [whole, fraction] = value.split(".");
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${fraction ? `.${fraction}` : ""}`;
};
const EMPTY_ARCHIVED_CANDLES: Extract<PositionChartSnapshot, { status: "ready" }>["chart_candles"] = [];

export default function PositionLevels({
  levels,
  quotePrice,
  timeframe,
  observedAt,
  algorithmVersion,
  marketId,
  analysisId,
  snapshotHash,
  chartSnapshot,
  chartLoading = false,
  chartError,
  onRetryChart,
}: {
  levels: Level[];
  quotePrice: string;
  timeframe: string;
  observedAt: string;
  algorithmVersion?: string;
  marketId: string;
  analysisId?: string;
  snapshotHash?: string;
  chartSnapshot?: PositionChartSnapshot | null;
  chartLoading?: boolean;
  chartError?: string | null;
  onRetryChart?: () => void;
}) {
  const reference = Number(quotePrice);
  const matching = matchingPositionSnapshot(chartSnapshot, {
    marketId, timeframe, quotePrice, observedAt, analysisId, snapshotHash,
  });
  const archivedCandles = matching && chartSnapshot?.status === "ready"
    ? chartSnapshot.chart_candles : EMPTY_ARCHIVED_CANDLES;
  const chartStatus = matching
    ? uiText("圖表保留本次分析時的行情，縮放或拖曳不會更新報告。")
    : chartLoading
      ? uiText("正在取得本次分析的 K 線快照…")
      : chartError
        ? uiText("無法取得本次分析的 K 線快照，請重新載入圖表。")
        : chartSnapshot?.status === "ready"
          ? positionSnapshotMessage("SNAPSHOT_REPORT_MISMATCH")
          : positionSnapshotMessage(chartSnapshot?.reason);
  return (
    <section className="position-levels" aria-label={uiText("本次持倉分析的支撐與壓力")}>
      <div className="position-levels-head">
        <div>
          <h3>{uiText("本次支撐與壓力")}</h3>
        </div>
        <span>
          {algorithmVersion === "confirmed_pivot_lifecycle_v3"
            ? "v3"
            : uiText("歷史版本")}{" "}
          · {timeframeLabel(timeframe)}
        </span>
      </div>
      <p>{uiText("分析時現價 $")}{price(quotePrice)} USDT ·{" "}
        {new Date(observedAt).toLocaleString(uiLocale(), { hour12: false })}
      </p>
      <div className="position-levels-chart">
        <div className="position-levels-chart-status" role="status" aria-live="polite">
          <span>{chartStatus}</span>
          {!matching && chartError && onRetryChart && (
            <button type="button" onClick={onRetryChart}>{uiText("重新載入圖表")}</button>
          )}
        </div>
        <CandlestickChart
          key={`${analysisId ?? snapshotHash ?? observedAt}:${marketId}:${timeframe}`}
          marketId={marketId}
          timeframe={timeframe}
          candles={archivedCandles}
          levels={levels}
          quotePrice={quotePrice}
          loading={chartLoading && !matching}
        />
      </div>
      {levels.length ? (
        levels.map((level, index) => {
          const relation =
            levelLocationLabel(level) ||
            (reference < Number(level.low)
              ? uiText("現價在區間下方")
              : reference > Number(level.high)
                ? uiText("現價在區間上方")
                : uiText("現價位於區間內"));
          return (
            <div
              className={`position-level${reference >= Number(level.low) && reference <= Number(level.high) ? " position-level-inside" : ""}`}
              key={
                level.id ?? `${level.kind}-${level.low}-${level.high}-${index}`
              }
            >
              <i className={level.kind} aria-hidden="true" />
              <div>
                <strong>
                  {level.kind === "support" ? uiText("支撐區") : uiText("壓力區")}
                </strong>
                <small>
                  {relation}
                  {level.pivot_count != null
                    ? " " + uiText("· {{p0}} 次確認轉折", { p0: level.pivot_count })
                    : ""}
                  {level.independent_touch_count != null
                    ? " " + uiText("· {{p0}} 次獨立觸及", { p0: level.independent_touch_count })
                    : ""}
                </small>
              </div>
              <b>
                ${price(level.low)} – ${price(level.high)}
              </b>
            </div>
          );
        })
      ) : (
        <p className="position-levels-empty">{uiText("本次報告沒有符合條件的支撐或壓力區。")}</p>
      )}
      <p className="position-levels-foot">{uiText("區間代表可能遇到阻力的位置，不保證反轉；持倉建議請以本次報告的價位與現價對照。")}</p>
    </section>
  );
}
