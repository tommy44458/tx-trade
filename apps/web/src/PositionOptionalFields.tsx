import { uiText } from "./i18n/index.ts";
export type OptionalPositionFields = {
  entry_time: string
  exchange_liquidation_price: string
  notes: string
}

export default function PositionOptionalFields({ value, onChange }: {
  value: OptionalPositionFields
  onChange: (patch: Partial<OptionalPositionFields>) => void
}) {
  return <>
    <label>{uiText("進場時間（選填，使用本地時間）")}<input type="datetime-local" value={value.entry_time} onChange={event => onChange({ entry_time: event.target.value })} /></label>
    <label>{uiText("交易所顯示的強平價（選填，USDT）")}<input inputMode="decimal" value={value.exchange_liquidation_price} onChange={event => onChange({ exchange_liquidation_price: event.target.value })} /></label>
    <label>{uiText("備註（選填，最多 500 字）")}<textarea maxLength={500} rows={3} value={value.notes} onChange={event => onChange({ notes: event.target.value })} /></label>
    <p className="note">{uiText("強平價僅記錄你從交易所抄錄的數值；市場與保證金變動後可能失效，請以交易所即時資料為準。備註不送入策略 Agent。")}</p>
  </>
}
