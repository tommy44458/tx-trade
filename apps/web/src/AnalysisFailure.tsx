import { uiText } from "./i18n/index.ts";
import './AnalysisFailure.css'
import { timeframeLabel } from './timeframes'

type Failure = { code?: string; message: string }

function explanation(error: Failure) {
  const message = error.message || ''
  if (message.includes('Agent entry decision lacks a clear reason')) return {
    stage: uiText("Agent 報告驗證"), field: uiText("entry_decision.reason（開單建議理由）"),
    detail: uiText("模型產生的開單理由未符合欄位格式。可能是空白、非文字或超過字數上限；舊版也會把理由中的數字價位誤判為無效。這次沒有產生可用的分析報告。"),
    next: uiText("更新後重新分析。若仍失敗，請提供下方任務編號與技術訊息，才能定位是哪個欄位持續不合格。"),
  }
  if (message.includes('Agent entry plan lacks trigger or invalidation')) return {
    stage: uiText("Agent 報告驗證"), field: 'entry_decision.trigger / invalidation',
    detail: uiText("開單方案的觸發條件或失效條件缺少有效文字。具體價位可以填寫，但不能只留空白。"),
    next: uiText("更新後重新分析；若同樣錯誤再出現，請提供任務編號。"),
  }
  if (message.includes('Tool timeframe differs from snapshot')) return {
    stage: uiText("指標計算檢核"), field: uiText("分析週期"),
    detail: uiText("模型要求的工具週期與本次行情快照不一致，因此系統拒絕混用數據。"),
    next: uiText("重新分析會建立新快照；若重複發生，請提供任務編號。"),
  }
  if (error.code === 'QUOTE_STALE' || error.code === 'CANDLES_STALE' || error.code === 'MODEL_RESULT_UNKNOWN') return {
    stage: error.code === 'MODEL_RESULT_UNKNOWN' ? uiText("模型呼叫") : uiText("行情新鮮度檢查"), field: null,
    detail: message,
    next: uiText("確認本地後端與行情來源可用，再重新分析。"),
  }
  if (error.code === 'MARKET_DATA_UNAVAILABLE') return {
    stage: uiText("行情來源未確認"), field: null,
    detail: message,
    next: uiText("確認本地後端與行情來源可用，再重新分析。"),
  }
  if (error.code === 'MODEL_VALIDATION_FAILED' || message.startsWith('報告或工具驗證未通過：')) return {
    stage: uiText("Agent 報告驗證"), field: null,
    detail: uiText("模型的報告或工具結果未符合系統檢核要求，沒有產生可用報告。具體驗證訊息見下方技術資訊。"),
    next: uiText("重新分析；若持續發生，請提供任務編號與技術訊息。"),
  }
  if (error.code === 'MODEL_CALL_FAILED' || message.includes('APITimeoutError')) return {
    stage: uiText("模型呼叫"), field: null,
    detail: uiText("模型連線、用量、設定或回應時間發生問題，分析未完成。"),
    next: uiText("檢查後端連線與 API 設定，再重新分析；若持續發生，請提供任務編號。"),
  }
  return {
    stage: uiText("分析流程"), field: null,
    detail: message || uiText("分析未完成。原始錯誤訊息與任務編號保留在下方，方便排查。"),
    next: uiText("重新分析；若仍失敗，請提供任務編號與技術訊息。"),
  }
}

export default function AnalysisFailure({ error, jobId, market, timeframe, kind }: {
  error: Failure | null
  jobId: string
  market: string
  timeframe: string
  kind: 'market' | 'positions'
}) {
  const failure = error ?? { code: 'UNKNOWN', message: uiText("分析失敗，但沒有回傳錯誤內容。") }
  const info = explanation(failure)
  return <div className="analysis-failure" role="alert">
    <strong>{kind === 'positions' ? uiText("持倉分析未完成") : uiText("市場分析未完成")}</strong>
    <div className="analysis-failure-facts"><span>{uiText("階段：")}{" "}{info.stage}</span><span>{market} · {timeframeLabel(timeframe)}</span></div>
    {info.field && <p><b>{uiText("失敗欄位：")}</b>{info.field}</p>}
    <p><b>{uiText("發生了什麼事：")}</b>{info.detail}</p>
    <p><b>{uiText("怎麼處理：")}</b>{info.next}</p>
    <details><summary>{uiText("查看技術資訊")}</summary><p>{uiText("錯誤代碼：")}{" "}{failure.code ?? uiText("未分類")}</p><p>{uiText("錯誤訊息：")}{" "}{failure.message}</p><p>{uiText("任務編號：")}<code>{jobId}</code></p></details>
  </div>
}
