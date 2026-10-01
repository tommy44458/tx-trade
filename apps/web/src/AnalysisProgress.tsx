import { uiText } from "./i18n/index.ts";
import "./AnalysisProgress.css";

export type AnalysisKind = "market" | "positions";

export function AnalysisSpinner() {
  return <span className="analysis-spinner" aria-hidden="true" />;
}

function phaseMessage(kind: AnalysisKind, phase: string) {
  switch (phase) {
    case "submitting":
      return uiText("正在送出分析請求…");
    case "queued":
      return uiText("分析已排隊，等待開始…");
    case "retrying":
      return uiText("正在重新取得行情資料…");
    case "macro":
      return uiText("正在取得共用的 AI 宏觀解讀；指標更新時會先重新分析…");
    case "fetching":
      return uiText("正在取得行情與市場背景…");
    case "calculating":
      return uiText("正在計算指標與支撐壓力…");
    case "model":
      return kind === "positions"
        ? uiText("AI 正在評估續抱或平倉，並整理理由…")
        : uiText("AI 正在評估交易策略，並整理理由…");
    default:
      return uiText("分析進行中，請稍候…");
  }
}

export default function AnalysisProgress({
  kind,
  phase,
}: {
  kind: AnalysisKind;
  phase: string;
}) {
  return (
    <div
      className="analysis-progress"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <AnalysisSpinner />
      <div>
        <strong>
          {kind === "positions" ? uiText("正在分析持倉") : uiText("正在分析市場")}
        </strong>
        <p>{phaseMessage(kind, phase)}</p>
      </div>
    </div>
  );
}
