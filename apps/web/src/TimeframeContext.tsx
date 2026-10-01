import { uiText, uiLocale } from "./i18n/index.ts";
import { orderedTimeframes, timeframeCode, type AnalysisTimeframe, type MarketTimeframe } from './timeframes'

export type TimeframeContextData = {
  primary_timeframe: AnalysisTimeframe; context_timeframe: MarketTimeframe
  primary_trend: string; context_trend: string | null; relation: string
  primary_last_candle_at: string; context_last_candle_at: string | null
  analysis_timeframes?: MarketTimeframe[]; context_timeframes?: MarketTimeframe[]
  contexts?: Record<string, { status: string; trend?: string; last_candle_at?: string }>
}

const trend = (value: string | null) => value === 'bullish' ? uiText("偏多") : value === 'bearish' ? uiText("偏空") : value === 'mixed' ? uiText("方向混合") : uiText("無資料")
const relation = (value: string) => ({ aligned: uiText("方向一致"), conflict: uiText("方向衝突"), range: uiText("方向混合"), uncertain: uiText("方向未確認"), unavailable: uiText("背景資料不足") })[value as 'aligned' | 'conflict' | 'range' | 'uncertain' | 'unavailable'] ?? value
const when = (value: string | null) => value ? new Date(value).toLocaleString(uiLocale(), { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'

export default function TimeframeContext({ context }: { context?: TimeframeContextData }) {
  if (!context) return null
  const contexts = context.contexts ?? { [context.context_timeframe]: {
    status: context.context_trend == null ? 'unavailable' : 'available',
    trend: context.context_trend ?? undefined, last_candle_at: context.context_last_candle_at ?? undefined,
  } }
  const frames = context.context_timeframes ?? orderedTimeframes(context.primary_timeframe, Object.keys(contexts))
  return <div className="timeframe-context">
    <strong>{uiText("跨週期背景")}</strong>
    <small>{uiText("主週期") + " "}{timeframeCode(context.primary_timeframe)}：{trend(context.primary_trend)}{" " + uiText("· 收盤") + " "}{when(context.primary_last_candle_at)}</small>
    {frames.map(tf => <small key={tf}>{uiText("輔助") + " "}{timeframeCode(tf)}：{trend(contexts[tf]?.trend ?? null)}{" " + uiText("· 收盤") + " "}{when(contexts[tf]?.last_candle_at ?? null)}</small>)}
    <small>{uiText("主週期與") + " "}{timeframeCode(context.context_timeframe)}：{relation(context.relation)}</small>
    <small>{uiText("方向採各週期 MA20／MA50 與已收盤價；EMA 為獨立指標。")}</small>
  </div>
}
