import { uiText, useUiLocale } from "./i18n/index.ts";
import { formatAmount, formatUsd } from "./smartMoney";

type Totals = { inflow: number; outflow: number; net: number; inflow_usd: number; outflow_usd: number; net_usd: number; whale_count: number };
type AssetFlows = { coverage?: string; "24h"?: Totals; "7d"?: Totals };

export type FundFlowsContextData = {
  status: "available" | "stale" | "unavailable";
  as_of?: string;
  assets?: Record<string, AssetFlows>;
  pair_asset?: AssetFlows & { asset: string; status: "tracked" | "not_tracked" | "same_as_reference" | "unavailable" };
  stablecoins?: { total_supply_usd: number; supply_change_1d_usd: number; exchange_net_24h_usd: number; exchange_net_7d_usd: number };
};

function Row({ asset, flows, locale }: { asset: string; flows: AssetFlows; locale: string }) {
  const day = flows["24h"];
  const week = flows["7d"];
  if (!day || !week) return null;
  return (
    <p>
      <b>{asset}</b>：{uiText("24 小時交易所淨流入 {{p0}} {{p1}}（{{p2}}）", {
        p0: formatAmount(day.net, locale), p1: asset, p2: formatUsd(day.net_usd, locale, true),
      })} · {uiText("7 天 {{p0}}", { p0: formatUsd(week.net_usd, locale, true) })} ·{" "}
      {uiText("巨鯨轉帳 {{p0}} 筆", { p0: day.whale_count })}
    </p>
  );
}

/** The fund flows an analysis saw, as context; the report's own reasoning says how they were weighed. */
export default function FundFlowsContext({ data }: { data?: FundFlowsContextData | null }) {
  const locale = useUiLocale();
  if (!data) return null;
  if (data.status === "unavailable" || !data.assets) return <p className="note">{uiText("資料未取得")}</p>;
  const pair = data.pair_asset;
  const stable = data.stablecoins;
  return (
    <>
      <p className="note">
        {uiText("鏈上資金只作為供給與參與程度的背景：流入交易所可能是準備賣出，流出可能是提領持有，但單筆用途無法確定。")}
        {data.status === "stale" && <> {uiText("鏈上資料延遲，以下數字可能不是最新。")}</>}
      </p>
      {Object.entries(data.assets).map(([asset, flows]) => <Row key={asset} asset={asset} flows={flows} locale={locale} />)}
      {pair && pair.status === "tracked" && <Row asset={pair.asset} flows={pair} locale={locale} />}
      {pair && pair.status === "not_tracked" && (
        <p className="note">{uiText("{{p0}} 不在以太坊上或尚未追蹤，沒有鏈上資料。", { p0: pair.asset })}</p>
      )}
      {stable && (
        <p>
          <b>{uiText("穩定幣")}</b>：{uiText("總供給 {{p0}}，24 小時 {{p1}}", {
            p0: formatUsd(stable.total_supply_usd, locale), p1: formatUsd(stable.supply_change_1d_usd, locale, true),
          })} · {uiText("24 小時交易所淨流入 {{p0}}", { p0: formatUsd(stable.exchange_net_24h_usd, locale, true) })}
        </p>
      )}
    </>
  );
}
