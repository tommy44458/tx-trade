import { pythonReferenceText } from "./pythonReferenceText";
import { uiText, uiLocale } from "./i18n/index.ts";
import { useEffect, useState } from 'react'

type CostAssumptions = { fee_bps_per_fill: string; slippage_bps_per_fill: string; source: string }

export type Strategy = {
  id?: string; type: string; side?: 'long' | 'short'; title: string; reason: string
  status?: string; confirmation_state?: string; entry_style?: "left" | "right"; entry_style_fit?: string; trigger?: string; trigger_met?: boolean
  entry?: string; stop_loss?: string; take_profit?: string; targets?: { price: string; net_risk_reward: string }[]
  risk_reward?: string; gross_risk_reward?: string; leverage?: number
  risk_on_theoretical_margin_pct?: string; level_algorithm_version?: string
  preference_fit?: string; risk_fit?: string; risk_metrics?: { net_reward_per_unit_usdt: string; net_loss_per_unit_usdt: string; cost_assumptions: CostAssumptions }
  invalidation?: string; counter_evidence?: string[]; expires_at?: string; event_exposure?: string
}

const fmt = (value?: string | number | null) => {
  if (value == null) return '—'
  const price = Number(value)
  const precision = Math.abs(price) >= 1000 ? 2 : Math.abs(price) >= 100 ? 3 : Math.abs(price) >= 1 ? 4 : 6
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: precision })
}
const when = (value?: string) => value ? new Date(value).toLocaleString(uiLocale(), { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
const kind = (value: string) => ({ wait: uiText("觀望"), pullback: uiText("趨勢回調"), breakout: uiText("突破確認"), range: uiText("區間測試") })[value as 'wait' | 'pullback' | 'breakout' | 'range'] ?? value

export default function StrategyCard({ strategy: s }: { strategy: Strategy }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!s.expires_at) return
    const delay = new Date(s.expires_at).getTime() - Date.now()
    if (delay <= 0) { setNow(Date.now()); return }
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(delay + 100, 2_147_483_647))
    return () => window.clearTimeout(timer)
  }, [s.expires_at])
  const reference = (text: string | undefined) => pythonReferenceText(text, { type: s.type, origin: "python" });
  const expired = !!s.expires_at && now >= new Date(s.expires_at).getTime()
  return <div className="strategy">
    <small>{s.type === 'wait' ? uiText("觀望 · 等待條件") : uiText("{{p0}} · {{p1}}情境", { p0: kind(s.type), p1: s.side === 'short' ? uiText("空頭") : uiText("多頭") })}</small>
    <h3>{reference(s.title)}</h3>
    <div className="scenario-why"><small>{uiText("情境依據")}</small><p>{reference(s.reason)}</p></div>
    {s.confirmation_state && <div className="scenario-status">{expired ? uiText("已到期，請重新分析") : s.confirmation_state === 'awaiting_close' ? (s.entry_style_fit === 'matched' ? uiText("右側：尚待所選週期收盤確認") : uiText("尚待所選週期收盤確認")) : s.confirmation_state === 'awaiting_zone_test' ? uiText("左側：區間測試待核對，尚無收盤確認") : uiText("目前觀望")}</div>}{" "}
    {s.trigger && <div className="trigger"><small>{uiText("觸發條件")}</small>{reference(s.trigger)}</div>}{" "}
    {s.invalidation && <div className="trigger"><small>{uiText("失效條件")}</small>{reference(s.invalidation)}</div>}{" "}
    {s.entry && <div className="prices">
      <div>{uiText("候選進場") + " "}<b>${fmt(s.entry)}</b></div>
      <div>{uiText("失效止損") + " "}<b>${fmt(s.stop_loss)}</b></div>
      <div>{uiText("首個目標") + " "}<b>${fmt(s.take_profit)}</b></div>
      <div>{s.risk_metrics ? uiText("含示例成本風報比") : uiText("風報比")} <b>{s.risk_reward}</b></div>
      {s.gross_risk_reward && <div>{uiText("未計成本風報比") + " "}<b>{s.gross_risk_reward}</b></div>}
      <div>{uiText("分析槓桿") + " "}<b>{s.leverage}×</b></div>
      <div>{uiText("理論保證金風險") + " "}<b>{s.risk_on_theoretical_margin_pct}%</b></div>
    </div>}
    {s.targets && s.targets.length > 1 && <div className="scenario-detail">{s.targets.map((target, index) => <small key={index}>{uiText("目標") + " "}{index + 1}：${fmt(target.price)}{" " + uiText("· 成本後風報比") + " "}{target.net_risk_reward}</small>)}</div>}
    {s.risk_metrics && <div className="cost-note">
      <small>{uiText("依據") + " "}{s.level_algorithm_version ?? uiText("歷史版本")} · {s.preference_fit === 'conflict' ? uiText("方向與你的判斷不同") : s.preference_fit === 'aligned' ? uiText("方向與你的判斷一致") : uiText("未設定方向偏好")}</small>
      {s.risk_fit && <small>{uiText("風險傾向篩選：")}{" "}{s.risk_fit === 'higher_confirmation' ? uiText("低風險，需更多獨立觸及") : s.risk_fit === 'balanced' ? uiText("中風險，基礎確認") : s.risk_fit === 'earlier_candidate' ? uiText("高風險，仍等待收盤") : uiText("未設定")}</small>}
      <small>{uiText("示例每次成交手續費") + " "}{s.risk_metrics.cost_assumptions.fee_bps_per_fill}{" " + uiText("bps、逆向滑價") + " "}{s.risk_metrics.cost_assumptions.slippage_bps_per_fill}{" " + uiText("bps；非帳戶實際費率，未含資金費或強平。")}</small>
      <small>{uiText("每單位淨獲利／淨損失情境：")}{" "}{fmt(s.risk_metrics.net_reward_per_unit_usdt)}／{fmt(s.risk_metrics.net_loss_per_unit_usdt)} USDT</small>
    </div>}
    {s.counter_evidence?.length ? <div className="scenario-detail"><strong>{uiText("反面證據與資料限制")}</strong>{s.counter_evidence.map((evidence, index) => <small key={index}>· {reference(evidence)}</small>)}</div> : null}{" "}
    {s.expires_at && <div className="scenario-expiry">{uiText("有效至") + " "}{when(s.expires_at)}{uiText("；到期後須重新分析")}</div>}
  </div>
}
