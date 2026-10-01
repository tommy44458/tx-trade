import { uiText, uiLocale } from "./i18n/index.ts";
export type DerivativesData = {
  status: string;
  source_url: string;
  timeframes?: Record<
    string,
    {
      status: string;
      observed_at?: string;
      open_interest_base_units?: string;
      change_24h_pct?: string | null;
    }
  >;
};

export default function DerivativesContext({
  data,
}: {
  data?: DerivativesData | null;
}) {
  if (!data) return null;
  return (
    <details className="secondary-evidence">
      <summary>{uiText("合約背景：未平倉量")}</summary>
      <p className="note">{uiText("未平倉量代表仍持有的合約總量，每筆都有多空雙方；不能單靠增加或減少判斷方向，也不是強平熱圖。")}</p>
      {orderedTimeframes("", Object.keys(data.timeframes ?? {})).map((frame) => {
        const value = data.timeframes?.[frame];
        return (
          <p key={frame}>
            <b>{timeframeCode(frame)}</b>：
            {value?.status === "available" ? (
              <>
                {Number(value.open_interest_base_units).toLocaleString(
                  uiLocale(),
                  { maximumFractionDigits: 2 },
                )}{" "}{uiText("基礎資產單位；")}{" "}{value.change_24h_pct == null
                  ? uiText("24 小時樣本不足")
                  : uiText("24 小時變化 {{p0}}%", { p0: value.change_24h_pct })}
                <small>
                  {" "}
                  · {new Date(value.observed_at!).toLocaleString(uiLocale())}
                </small>
              </>
            ) : (
              uiText("資料未取得")
            )}
          </p>
        );
      })}
      <a href={data.source_url} target="_blank" rel="noreferrer">{uiText("Binance 公開統計資料說明")}</a>
    </details>
  );
}
import { orderedTimeframes, timeframeCode } from "./timeframes";
