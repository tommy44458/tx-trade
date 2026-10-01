import { uiText, uiLocale } from "./i18n/index.ts";
import "./EventPanel.css";

export type NewsItem = {
  id: string;
  version: number;
  source: string;
  source_url: string;
  title: string;
  summary: string;
  published_at: string;
  ingested_at: string;
  metadata: { kind: string; classification: string; interpretation: string };
};
export type NewsContext = {
  status: "available" | "no_recent_news" | "offline";
  source_status: Record<string, "ok" | "offline">;
  source_checked_at: string | null;
  cutoff: string;
  coverage_hours: number;
  risk: "none" | "recent_fomc_release";
  items: NewsItem[];
  archive: NewsItem[];
  classification_status: string;
  evidence_pack?: {
    coverage_status: "partial" | "unknown";
    lookback_hours: number;
    eligible_event_count: number;
    excluded_count: number;
    exclusion_reasons: Record<string, number>;
    events: Array<{
      event_id: string;
      published_at: string;
      verification: string;
      citation: { title: string; source_url: string };
    }>;
  };
};

const exclusionLabels: Record<string, string> = {
  get sec_excerpt_not_full_release() { return uiText("SEC 僅有截斷摘要"); },
  get pending_classification() { return uiText("尚未完成分類"); },
  get not_promoted_for_strategy() { return uiText("尚未通過事件查證"); },
  get invalid_source_url() { return uiText("來源網址未通過驗證"); },
};

export default function NewsPanel({
  context,
}: {
  context: NewsContext | null | undefined;
}) {
  return (
    <section className="panel event-panel">
      <div className="panel-head">
        <div>
          <h2>{uiText("近一個月官方新聞覆蓋")}</h2>
        </div>
        <span className="tag">{uiText("分析快照")}</span>
      </div>
      {!context ? (
        <p className="event-note">{uiText("這份分析沒有新聞風險快照。")}</p>
      ) : (
        <>
          {context.risk === "recent_fomc_release" && (
            <div className="warning">{uiText("官方 FOMC 聲明發布後 15 分鐘內，風險規則暫停新進場候選；未依聲明標題判定政策方向。")}</div>
          )}{" "}
          {!context.evidence_pack ? (
            <p className="event-note">{uiText("這份歷史分析尚未建立事件證據包；請重新按分析以取得目前的來源查證與排除原因。")}</p>
          ) : (
            <>
              <p className="event-note">{uiText("此處記錄官方公告是否發布；宏觀方向與原因請看上方 Agent 分析。Jev 分類只供篩選，未查證摘要不作方向依據。")}</p>
              {context.evidence_pack.events.map((event) => (
                <p className="event-note" key={event.event_id}>{uiText("已核對發布：")}<a
                    href={event.citation.source_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {event.citation.title}
                  </a>
                  （{new Date(event.published_at).toLocaleString(uiLocale())}{" "}{uiText("）。僅確認公告已發布。")}</p>
              ))}
              <p className="event-note">{uiText("近")}{" "}{Math.round(context.evidence_pack.lookback_hours / 24)}{" "}{uiText("天證據包：")}{" "}{context.evidence_pack.eligible_event_count}{" "}{uiText("個可引用發布事件；")}{" "}{context.evidence_pack.excluded_count}{" "}{uiText("個候選未採用。")}{" "}{context.evidence_pack.coverage_status === "unknown"
                  ? uiText("來源覆蓋不完整。")
                  : uiText("來源僅涵蓋目前接入的官方公告。")}
              </p>
              {Object.entries(context.evidence_pack.exclusion_reasons).map(
                ([reason, count]) => (
                  <p className="event-note" key={reason}>{uiText("排除原因：")}{" "}{exclusionLabels[reason] ?? reason}（{count}）
                  </p>
                ),
              )}
            </>
          )}
          <p className="event-note">{uiText("來源狀態：Fed")}{" "}
            {context.source_status.fed_monetary_rss === "ok"
              ? uiText("已同步")
              : uiText("離線／過期")}{" "}
            {context.source_status.sec_press_rss
              ? `；SEC ${context.source_status.sec_press_rss === "ok" ? uiText("已同步") : uiText("離線／過期")}`
              : ""}{uiText("。目前仍無法代表整體市場新聞覆蓋。")}</p>
          {context.status === "offline" && (
            <div className="warning">{uiText("Fed 消息來源離線或過期，新聞覆蓋不明，不能判定近期沒有其他市場事件。")}</div>
          )}
        </>
      )}
    </section>
  );
}
