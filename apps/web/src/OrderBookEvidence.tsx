import { uiText, uiLocale } from "./i18n/index.ts";
type Zone = { side: 'bid' | 'ask'; low: string; high: string; notional_usdt: string; relative_to_median_bin: string; side_share_pct: string }
export type BookEvidence = { status: string; zones: Zone[]; observed_at?: string; age_seconds?: number; coverage?: Record<string, { low: string; high: string; bins: number }> }

const money = (value: string) => Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 })

export default function OrderBookEvidence({ evidence }: { evidence?: BookEvidence }) {
  if (!evidence) return null
  return <div className="book-evidence">
    <h3>{uiText("委託簿掛單密度") + " "}<small>{uiText("單次快照")}</small></h3>
    {evidence.status !== 'snapshot' ? <p>{uiText("目前無可用的委託簿快照；歷史支撐壓力仍可獨立閱讀。")}</p> : <>
      <p>{uiText("觀測於") + " "}{evidence.observed_at ? new Date(evidence.observed_at).toLocaleString(uiLocale(), { hour12: false }) : '—'}{uiText("。只反映 Binance 近價買盤／賣盤掛單；無法得知多空持倉，掛單也可能隨時撤銷。")}</p>
      {evidence.zones.length ? evidence.zones.map((zone, index) => <div className="book-zone" key={`${zone.side}-${index}`}>
        <span>{zone.side === 'bid' ? uiText("買盤掛單") : uiText("賣盤掛單")} · ${money(zone.low)}–${money(zone.high)}</span>
        <small>{uiText("約 $")}{money(zone.notional_usdt)}{" " + uiText("USDT · 同側中位區間的") + " "}{zone.relative_to_median_bin}{" " + uiText("倍")}</small>
      </div>) : <p>{uiText("快照覆蓋範圍內沒有達到密集門檻的價格區。")}</p>}
      {evidence.coverage && <small className="book-coverage">{uiText("可見範圍：買盤 $")}{money(evidence.coverage.bid?.low ?? '0')}–${money(evidence.coverage.bid?.high ?? '0')}{uiText("；賣盤 $")}{money(evidence.coverage.ask?.low ?? '0')}–${money(evidence.coverage.ask?.high ?? '0')}{uiText("。超出範圍無法判斷掛單密度。")}</small>}
    </>}
  </div>
}
