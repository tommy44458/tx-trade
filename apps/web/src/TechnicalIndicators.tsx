import { uiText, uiLocale } from "./i18n/index.ts";
import './TechnicalIndicators.css'
import { contextTimeframes, orderedTimeframes, snapshotTimeframes, timeframeCode, timeframeLabel, type AnalysisTimeframe } from './timeframes'

type Frame = {
  status: 'available' | 'unavailable'; last_closed_at?: string; candle_count?: number
  metrics: Record<string, unknown>; indicators: Record<string, Record<string, unknown>>
}
type HigherFrame = Frame & {
  duration_days?: string; coverage_status?: string
  structure?: { state: string }
  range_windows?: Array<{ requested_days: number; duration_days: string; status: string; high: string; low: string; quote_position_pct: string | null }>
}
export type TechnicalSnapshot = {
  version: string; data_basis: 'closed_candles'; primary_timeframe: AnalysisTimeframe; as_of: string
  analysis_timeframes?: string[]; context_timeframes?: string[]
  initial_indicator_selection?: { names: string[]; parameters: Record<string, Record<string, unknown>> }
  timeframes: Record<string, Frame>
  higher_timeframe_context?: { timeframes: Record<string, HigherFrame> }
}
export type AnalysisExecution = {
  model_requests: number; additional_tool_calls: number; input_tokens: number | null; output_tokens: number | null
}

function number(value: unknown): string {
  if (value == null || value === '') return '—'
  const parsed = Number(value)
  return Number.isFinite(parsed)
    ? parsed.toLocaleString('en-US', { maximumSignificantDigits: 8 }) : '—'
}

const direction = (value: unknown) => value === 'bullish' ? uiText("偏多") : value === 'bearish' ? uiText("偏空") : value === 'mixed' ? uiText("混合") : uiText("方向未明")
const pair = (a: unknown, b: unknown) => `${number(a)} ／ ${number(b)}`
const initialIndicatorLabels: Record<string, string> = { bollinger: 'Bollinger', fibonacci: 'Fibonacci', adx_dmi: 'ADX / DMI', obv: 'OBV', donchian: 'Donchian', keltner: 'Keltner', stochastic: 'Stochastic' }
type IndicatorRow = { label: string; indicator?: string; read: (frame: Frame) => string }
const rows: IndicatorRow[] = [
  { get label() { return uiText("上一根收盤"); }, read: f => number(f.metrics.last_close) },
  { label: 'MA20 ／ MA50', read: f => `${pair(f.metrics.ma20, f.metrics.ma50)}（${direction(f.metrics.trend)}）` },
  { label: 'EMA20 ／ EMA50', indicator: 'trend_ema', read: f => `${pair(f.indicators.trend_ema.ema20, f.indicators.trend_ema.ema50)}（${direction(f.indicators.trend_ema.direction)}）` },
  { label: 'RSI（14）', indicator: 'rsi', read: f => number(f.indicators.rsi.value) },
  { get label() { return uiText("MACD 主線 ／ 訊號 ／ 柱體"); }, indicator: 'macd', read: f => `${pair(f.indicators.macd.line, f.indicators.macd.signal)} ／ ${number(f.indicators.macd.histogram)}` },
  { get label() { return uiText("布林下緣 ／ 中線 ／ 上緣（20）"); }, indicator: 'bollinger', read: f => `${pair(f.indicators.bollinger.lower, f.indicators.bollinger.middle)} ／ ${number(f.indicators.bollinger.upper)}` },
  { label: 'ATR（14）', indicator: 'volatility_atr', read: f => `${number(f.indicators.volatility_atr.atr)}（${number(f.indicators.volatility_atr.atr_pct)}%）` },
  { get label() { return uiText("成交量 ／ 前 20 根均量（幣）"); }, indicator: 'volume_signal', read: f => pair(f.indicators.volume_signal.last_volume, f.indicators.volume_signal.average_previous_20) },
  { get label() { return uiText("相對成交量"); }, indicator: 'volume_signal', read: f => f.indicators.volume_signal.relative_volume == null ? uiText("無有效均量") : uiText("{{p0}} 倍", { p0: number(f.indicators.volume_signal.relative_volume) }) },
  { get label() { return uiText("滾動 VWAP（20）"); }, indicator: 'rolling_vwap', read: f => number(f.indicators.rolling_vwap.rolling_vwap) },
  { get label() { return uiText("最近確認波段（高 ／ 低）"); }, indicator: 'swing_points', read: f => {
    const points = (f.indicators.swing_points.recent_points ?? []) as Array<{ kind: string; price: string }>
    const highs = points.filter(p => p.kind === 'high')
    const lows = points.filter(p => p.kind === 'low')
    return pair(highs.at(-1)?.price, lows.at(-1)?.price)
  } },
  { label: 'Fibonacci（38.2% ／ 50% ／ 61.8%）', indicator: 'fibonacci', read: f => {
    const indicator = f.indicators.fibonacci
    const current = indicator.retracements as Record<string, { price?: unknown }> | undefined
    const legacy = indicator.retracements_from_high as Record<string, unknown> | undefined
    const price = (ratio: string) => current?.[ratio]?.price ?? legacy?.[ratio]
    return `${pair(price('0.382'), price('0.5'))} ／ ${number(price('0.618'))}`
  } },
  { label: 'ADX ／ +DI ／ −DI', indicator: 'adx_dmi', read: f => `${pair(f.indicators.adx_dmi.adx, f.indicators.adx_dmi.plus_di)} ／ ${number(f.indicators.adx_dmi.minus_di)}` },
  { label: 'OBV Δ ／ %', indicator: 'obv', read: f => `${number(f.indicators.obv.obv_change)} ／ ${f.indicators.obv.signed_volume_fraction == null ? '—' : number(Number(f.indicators.obv.signed_volume_fraction) * 100) + '%'}` },
  { get label() { return uiText("唐奇安通道"); }, indicator: 'donchian', read: f => pair(f.indicators.donchian.lower, f.indicators.donchian.upper) },
  { get label() { return uiText("肯特納通道"); }, indicator: 'keltner', read: f => `${pair(f.indicators.keltner.lower, f.indicators.keltner.middle)} ／ ${number(f.indicators.keltner.upper)}` },
  { label: 'Stochastic K ／ D', indicator: 'stochastic', read: f => pair(f.indicators.stochastic.k, f.indicators.stochastic.d) },
]

function readRow(row: IndicatorRow, frame?: Frame): string {
  if (frame?.status !== 'available') return '—'
  if (row.indicator) {
    const indicator = frame.indicators?.[row.indicator]
    if (!indicator) return uiText("資料未提供")
    if (indicator.status === 'unavailable') return indicator.reason === 'zero_volume' ? uiText("無成交量，無法計算")
      : indicator.reason === 'no_confirmed_directional_pivot_pair' ? uiText("缺少已確認波段，無法計算。")
      : indicator.reason === 'needs_80_closed_candles' ? uiText("不足 80 根已收盤資料") : uiText("資料不足，無法計算")
  }
  return row.read(frame)
}

export default function TechnicalIndicators({ snapshot, execution }: {
  snapshot: TechnicalSnapshot; execution?: AnalysisExecution | null
}) {
  const higherFrames = snapshot.higher_timeframe_context?.timeframes ?? {}
  const frames: Record<string, Frame> = { ...higherFrames, ...snapshot.timeframes }
  const analysisFrames = snapshotTimeframes(snapshot.primary_timeframe, snapshot.analysis_timeframes, Object.keys(frames))
  const contextFrames: readonly string[] = snapshot.context_timeframes ?? contextTimeframes(snapshot.primary_timeframe)
  const backgroundFrames = orderedTimeframes(snapshot.primary_timeframe, Object.keys(higherFrames))
    .filter(tf => tf !== snapshot.primary_timeframe)
  const initialNames = snapshot.initial_indicator_selection?.names ?? []
  const advanced = new Set(['bollinger', 'fibonacci', 'adx_dmi', 'obv', 'donchian', 'keltner', 'stochastic'])
  const visibleRows = rows.filter(row => !row.indicator || !advanced.has(row.indicator)
    || analysisFrames.some(tf => frames[tf]?.indicators?.[row.indicator!]))
  return <details className="technical-indicators" open>
    <summary>{uiText("技術指標與大週期背景")}</summary>
    <p>{uiText("常用指標已預先計算，一次提供給 AI 判斷。下表使用已收盤 K 線，盤中現價另行分析。")}</p>
    {!!initialNames.length && <p>{uiText("本次首批指標：{{p0}}", { p0: initialNames.map(name => initialIndicatorLabels[name] ?? name).join(' · ') })}</p>}
    {execution && <p className="technical-execution">{uiText("本次模型請求") + " "}{execution.model_requests}{" " + uiText("次；Agent 追加工具") + " "}{execution.additional_tool_calls}{" "}{uiText("次。")}{execution.input_tokens != null && execution.output_tokens != null && " " + uiText("輸入 {{p0}} tokens，輸出 {{p1}} tokens。", { p0: execution.input_tokens.toLocaleString(uiLocale()), p1: execution.output_tokens.toLocaleString(uiLocale()) })}</p>}
    <div className="technical-table-scroll" role="region" aria-label={uiText("各週期技術指標")} tabIndex={0}><table><thead><tr><th scope="col">{uiText("指標")}</th>{analysisFrames.map(tf =>
      <th scope="col" key={tf}>{timeframeCode(tf)} <small>{snapshot.primary_timeframe === tf ? uiText("主週期") : contextFrames.includes(tf) ? uiText("大方向輔助") : uiText("其他週期")}</small>
        <span>{frames[tf]?.status === 'available'
          ? uiText("{{p0}} 根；收盤 {{p1}}", { p0: frames[tf].candle_count, p1: new Date(frames[tf].last_closed_at!).toLocaleString(uiLocale(), { hour12: false }) })
          : uiText("資料未提供")}</span></th>)}</tr></thead><tbody>{visibleRows.map(row => <tr key={row.label}>
            <th scope="row">{row.label}</th>{analysisFrames.map(tf => <td key={tf}>
              {readRow(row, frames[tf])}</td>)}</tr>)}</tbody></table></div>
    <small>{uiText("趨勢標籤只供參考；相關指標不代表多份獨立證據。完整精度、參數與確認時間可在下方計算紀錄查核。")}</small>
    {!!backgroundFrames.length && <>
      <h3>{backgroundFrames.map(timeframeCode).join('／')}{uiText("：大方向與價格位置")}</h3>
      <p>{uiText("用來區分大趨勢與短線進場機會。區間位置 0% 是該段最低、100% 是最高；高位本身不代表要反轉。")}</p>
      <div className="technical-table-scroll" role="region" aria-label={uiText("大週期價格位置")} tabIndex={0}><table><thead><tr><th scope="col">{uiText("背景")}</th>
        {backgroundFrames.map(tf => {
          const f = higherFrames[tf]
          return <th scope="col" key={tf}>{timeframeLabel(tf)}（{timeframeCode(tf)}）<span>{f.status === 'available'
            ? uiText("{{p0}} 根；涵蓋 {{p1}} 天{{p2}}；收盤 {{p3}}", { p0: f.candle_count, p1: f.duration_days, p2: f.coverage_status === 'partial' ? uiText("（資料不足）") : '', p3: new Date(f.last_closed_at!).toLocaleString(uiLocale(), { hour12: false }) })
            : uiText("背景資料未取得")}</span></th>
        })}</tr></thead><tbody>
        <tr><th scope="row">{uiText("確認波段結構")}</th>{backgroundFrames.map(tf => {
          const f = higherFrames[tf]
          return <td key={tf}>{f.status !== 'available' ? '—' : ({ rising: uiText("高低點抬高"), falling: uiText("高低點降低"), mixed: uiText("高低點混合"), insufficient: uiText("確認轉折不足") }[f.structure?.state ?? 'insufficient'])}</td>
        })}</tr>
        {[7, 30, 90, 180].map(days => <tr key={days}><th scope="row">{uiText("近 {{p0}} 天低／高；現價位置", { p0: days })}</th>
          {backgroundFrames.map(tf => {
            const f = higherFrames[tf]
            const w = f.range_windows?.find(w => w.requested_days === days)
            return <td key={tf}>{f.status === 'available' && w ? <>{pair(w.low, w.high)}；{w.quote_position_pct == null ? uiText("區間無價差") : `${number(w.quote_position_pct)}%`}
              {w.status === 'partial' && <small className="technical-range-note">{uiText("僅涵蓋") + " "}{w.duration_days}{" " + uiText("天")}</small>}</> : '—'}</td>
          })}</tr>)}
      </tbody></table></div>
    </>}
  </details>
}
