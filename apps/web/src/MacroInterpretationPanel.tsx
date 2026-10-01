import { economicMetricLabel } from "./economicLabels";
import { uiText, uiLocale, useUiLocale, languageName, type UiLocale } from "./i18n/index.ts";
import "./MacroInterpretationPanel.css";

export type MacroInterpretationEvidence = {
  id: string;
  type:
    | "official_actual"
    | "fomc_target_range"
    | "official_publication"
    | "market_consensus";
  kind?: string;
  metric?: string;
  label?: string;
  title?: string;
  source: string;
  source_url: string;
  version?: number | null;
  period?: string;
  value?: string | null;
  unit?: string;
  previous_value?: string | null;
  published_at: string | null;
  observed_at: string;
  time_basis?: "official_release" | "first_observed";
  decision?: string;
  lower_pct?: string;
  upper_pct?: string;
  actual?: string | null;
  forecast?: string | null;
  previous?: string | null;
};

export type MacroInterpretationCoverage = {
  official_actual_kinds?: string[];
  missing_actuals?: string[];
  consensus_status?: string;
  excluded_count?: number;
  freshness?: { id: string; reference_at: string; within_last_month: boolean }[];
  source_status?: Record<string, Record<string, string>>;
  interpretation_limit?: string;
  scope?: Record<string, unknown>;
};

export type MacroInterpretation = {
  id: string;
  output_locale?: UiLocale;
  response_locale?: UiLocale;
  source_locale?: UiLocale;
  dataset_version: string;
  as_of: string;
  generated_at: string;
  outlook: {
    stance: "bullish" | "bearish" | "neutral";
    summary: string;
  };
  drivers: {
    title: string;
    explanation: string;
    evidence_ids: string[];
  }[];
  uncertainties: string[];
  watch_next: string[];
  evidence: MacroInterpretationEvidence[];
  provider: string;
  model: string;
  prompt_version: string;
};

export type MacroInterpretationStatus = {
  version: "macro_interpretation_cache_v1";
  status:
    | "missing"
    | "running"
    | "succeeded"
    | "failed"
    | "unconfigured"
    | "insufficient";
  stale: boolean;
  needs_update: boolean;
  dataset_version: string;
  as_of: string;
  evidence_count: number;
  coverage: MacroInterpretationCoverage;
  interpretation: MacroInterpretation | null;
  error: { code: string; message: string; retryable: boolean } | null;
  cached: boolean;
  requested_locale?: UiLocale;
  display_locale?: UiLocale;
  translation_available?: boolean;
  translation?: {
    status: "available" | "missing" | "queued" | "running" | "failed";
    target_locale: UiLocale;
    error?: { code: string; message: string; retryable?: boolean } | null;
    job_id?: string | null;
  };
};

export type MacroInterpretationPanelProps = {
  interpretation?: MacroInterpretationStatus | null;
  ensuring?: boolean;
  error?: string | null;
  onEnsure?: () => void;
  onTranslate?: () => void;
  translating?: boolean;
};

const stanceLabels = {
  get bullish() { return uiText("偏多"); },
  get bearish() { return uiText("偏空"); },
  get neutral() { return uiText("中性"); },
};
const statusLabels = {
  get missing() { return uiText("尚無解讀"); },
  get running() { return uiText("分析中"); },
  get succeeded() { return uiText("已完成"); },
  get failed() { return uiText("分析未完成"); },
  get unconfigured() { return uiText("尚未啟用"); },
  get insufficient() { return uiText("指標不足"); },
};
const decisions: Record<string, string> = {
  get raise() { return uiText("調升"); },
  get lower() { return uiText("調降"); },
  get maintain() { return uiText("維持"); },
};
const sourceLabels: Record<string, string> = {
  bls: "BLS",
  bea: "BEA",
  fed: "Fed",
  fed_monetary_rss: "Fed",
};
const metricKinds: Record<string, string> = {
  cpi: "CPI",
  get employment() { return uiText("就業"); },
  pce: "PCE",
  gdp: "GDP",
};

function stamp(value: string | null | undefined) {
  if (!value) return uiText("未提供");
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString(uiLocale(), {
        timeZone: "Asia/Taipei",
        hour12: false,
      })
    : uiText("未提供");
}

function officialUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

function withUnit(value: string, unit?: string) {
  const suffix =
    unit === "percent"
      ? "%"
      : unit === "thousand_jobs"
        ? " " + uiText("千人")
        : unit
          ? ` ${unit}`
          : "";
  return `${value}${suffix}`;
}

function evidenceLabel(item: MacroInterpretationEvidence) {
  if (item.label) return economicMetricLabel(item.label, item.metric);
  if (item.type === "official_publication") return uiText("FOMC 聲明發布");
  if (item.type === "market_consensus") return uiText("市場預期資料");
  return uiText("FOMC 利率決策");
}

function evidenceValue(item: MacroInterpretationEvidence) {
  if (item.value != null) return withUnit(item.value, item.unit);
  if (item.type === "official_publication") return uiText("僅確認聲明發布");
  if (item.type === "market_consensus") {
    if (item.actual != null) return uiText("實際 {{p0}}", { p0: withUnit(item.actual, item.unit) });
    if (item.forecast != null) return uiText("預期 {{p0}}", { p0: withUnit(item.forecast, item.unit) });
  }
  if (item.lower_pct != null && item.upper_pct != null)
    return `${decisions[item.decision ?? ""] ?? uiText("目標利率")} ${item.lower_pct}–${item.upper_pct}%`;
  return uiText("未提供數值");
}

export default function MacroInterpretationPanel({
  interpretation: state,
  ensuring = false,
  error,
  onEnsure,
  onTranslate,
  translating = false,
}: MacroInterpretationPanelProps) {
  const locale = useUiLocale();
  const result = state?.interpretation;
  const displayLocale = result?.response_locale ?? result?.output_locale ?? state?.display_locale ?? "zh-TW";
  const translationBusy = translating || ["queued", "running"].includes(state?.translation?.status ?? "");
  const translationMissing = !!result && displayLocale !== locale;

  const missingActuals = state?.coverage.missing_actuals ?? [];
  const busy = ensuring || state?.status === "running";
  const canEnsure =
    onEnsure &&
    state &&
    !busy &&
    state.status !== "insufficient" &&
    (state.needs_update ||
      state.stale ||
      state.status === "missing" ||
      state.status === "failed" ||
      state.status === "unconfigured");
  const statusLabel = ensuring
    ? uiText("等待分析")
    : !state
      ? error
        ? uiText("讀取失敗")
        : uiText("讀取中")
      : state.stale && state.status === "succeeded"
        ? uiText("資料已更新")
        : statusLabels[state.status];
  let statusNote = uiText("正在讀取已儲存的 AI 宏觀解讀。");
  if (ensuring)
    statusNote = uiText("正在準備最新資料的解讀，完成後會在此顯示。");
  else if (state?.status === "running")
    statusNote = uiText("AI 正在解讀最新官方指標，完成後會自動更新。");
  else if (state?.status === "unconfigured")
    statusNote = uiText("AI 宏觀解讀尚未啟用，仍可查看下方官方數據與公布日程。");
  else if (state?.status === "insufficient")
    statusNote = uiText("目前缺少可引用的官方指標，取得足夠資料後才會進行 AI 解讀。");
  else if (state?.status === "failed")
    statusNote = uiText("本次 AI 解讀未完成，可重試讀取最新資料的解讀。");
  else if (state?.stale)
    statusNote = uiText("官方指標已有更新，這份解讀使用前一版資料，等待更新解讀。");
  else if (state?.status === "missing")
    statusNote = uiText("這批官方指標尚無 AI 解讀，產生後將保存並與交易分析共用。");
  else if (state?.status === "succeeded")
    statusNote = uiText("此解讀已儲存並與交易分析共用；指標新增或更新時才重新分析。");
  else if (error)
    statusNote = uiText("暫時無法讀取 AI 宏觀解讀，請重新整理頁面。");

  return (
    <section
      className="panel macro-interpretation-panel"
      aria-label={uiText("AI 宏觀解讀")}
      aria-busy={busy}
    >
      <div className="panel-head macro-panel-head">
        <h2>{uiText("AI 宏觀解讀")}</h2>
        <span className={`tag macro-status ${busy ? "macro-status-busy" : ""}`}>
          {statusLabel}
        </span>
      </div>
      <p className="macro-status-note" role="status" aria-live="polite">
        {statusNote}
        {result && state?.stale && busy && (
          <span>{" " + uiText("以下保留前一版解讀供查看。")}</span>
        )}
      </p>
      {(error || state?.error?.message) && (
        <p className="warning macro-error" role="alert">
          {error || state?.error?.message}
        </p>
      )}
      {state && !result && (
        <div className="macro-empty-context">
          <p>{uiText("可用依據")}{" "}{uiText("evidenceCount", { count: state.evidence_count })}{!!missingActuals.length && " " + uiText("· 尚缺 {{p0}} 官方實際值", { p0: missingActuals.map((kind) => metricKinds[kind] ?? kind).join("、") })}
          </p>
          <details className="macro-provenance">
            <summary>{uiText("查看資料版本")}</summary>
            <dl className="macro-version-meta">
              <div><dt>{uiText("資料截至")}</dt><dd><time dateTime={state.as_of}>{stamp(state.as_of)}</time></dd></div>
              <div><dt>{uiText("資料版本")}</dt><dd className="macro-identity">{state.dataset_version}</dd></div>
            </dl>
          </details>
        </div>
      )}
      {result && (
        <>
          <div className="macro-language-tools">
            <span>{uiText("顯示語言")}: {languageName(displayLocale)}{" "}{translationMissing ? ` · ${uiText("原文")}` : ""}</span>
            {translationMissing && onTranslate && <button type="button" className="secondary-button" disabled={translationBusy} aria-busy={translationBusy} onClick={onTranslate}>
              {translationBusy ? uiText("翻譯中…") : state?.translation?.status === "failed" ? uiText("重試翻譯") : uiText("翻譯已存解讀")}
            </button>}
          </div>
          {translationMissing && <p className="macro-status-note">{uiText("目前顯示已儲存的原文；翻譯只改文字，不會重新判斷宏觀方向。")}</p>}{" "}
          {state?.translation?.status === "failed" && <p className="warning" role="alert">{uiText("翻譯未完成。原始解讀已保留。")}{" "} {state.translation.error?.message}</p>}

          <div className={`macro-outlook ${result.outlook.stance}`}>
            <div className="macro-outlook-heading">
              <span>{uiText("風險資產的宏觀背景")}</span>
              <strong className="macro-stance">
                {stanceLabels[result.outlook.stance]}
              </strong>
              {state?.stale && <small>{uiText("前一版資料解讀")}</small>}
            </div>
            <p lang={displayLocale}>{result.outlook.summary}</p>
          </div>
          {!!result.drivers.length && (
            <div className="macro-drivers">
              {result.drivers.map((driver, index) => {
                const evidence = result.evidence.filter((item) =>
                  driver.evidence_ids.includes(item.id),
                );
                return (
                  <article className="macro-driver" key={`${index}:${driver.title}`}>
                    <h3 lang={displayLocale}>{driver.title}</h3>
                    <p lang={displayLocale}>{driver.explanation}</p>
                    {!!evidence.length && (
                      <div className="macro-driver-evidence" aria-label={uiText("引用的指標")}>
                        {evidence.map((item) => {
                          const href = officialUrl(item.source_url);
                          const label = evidenceLabel(item);
                          return href ? (
                            <a key={item.id} href={href} target="_blank" rel="noreferrer">
                              {label} · {evidenceValue(item)} ↗
                            </a>
                          ) : (
                            <span key={item.id}>{label} · {evidenceValue(item)}</span>
                          );
                        })}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}{" "}
          {!!result.uncertainties.length && (
            <div className="macro-uncertainty">
              <h3>{uiText("仍待確認")}</h3>
              <ul>
                {result.uncertainties.map((item, index) => <li key={index} lang={displayLocale}>{item}</li>)}
              </ul>
            </div>
          )}{" "}
          {!!result.watch_next.length && (
            <div className="macro-uncertainty macro-watch-next">
              <h3>{uiText("接下來觀察")}</h3>
              <ul>
                {result.watch_next.map((item, index) => <li key={index} lang={displayLocale}>{item}</li>)}
              </ul>
            </div>
          )}
          <details className="macro-provenance">
            <summary>{uiText("引用依據與解讀版本") + " "}<span>{uiText("evidenceCount", { count: result.evidence.length })}</span></summary>
            <dl className="macro-version-meta">
              <div><dt>{uiText("資料截至")}</dt><dd><time dateTime={result.as_of}>{stamp(result.as_of)}</time></dd></div>
              <div><dt>{uiText("解讀完成")}</dt><dd><time dateTime={result.generated_at}>{stamp(result.generated_at)}</time></dd></div>
              <div><dt>{uiText("資料版本")}</dt><dd className="macro-identity">{result.dataset_version}</dd></div>
              <div><dt>{uiText("解讀編號")}</dt><dd className="macro-identity">{result.id}</dd></div>
              <div><dt>{uiText("分析模型")}</dt><dd>{result.provider} · {result.model}</dd></div>
              <div><dt>{uiText("解讀規格")}</dt><dd className="macro-identity">{result.prompt_version}</dd></div>
            </dl>
            {!!state?.stale && (
              <div className="macro-current-dataset">{uiText("最新資料版本")}<span className="macro-identity">{state.dataset_version}</span>
                <span>{uiText("資料截至") + " "}{stamp(state.as_of)}</span>
              </div>
            )}
            <div className="macro-official-evidence">
              {result.evidence.map((item) => {
                const href = officialUrl(item.source_url);
                return (
                  <article key={item.id}>
                    <div className="macro-evidence-heading">
                      <h4>{evidenceLabel(item)}</h4>
                      <strong>{evidenceValue(item)}</strong>
                    </div>
                    <p>
                      {item.period && <span>{uiText("資料期間") + " "}{item.period}</span>}{" "}
                      {item.previous_value != null && <span>{uiText("前期") + " "}{withUnit(item.previous_value, item.unit)}</span>}{" "}
                      {item.type === "market_consensus" && item.actual != null && item.forecast != null && <span>{uiText("市場預期") + " "}{withUnit(item.forecast, item.unit)}</span>}{" "}
                      {item.type === "market_consensus" && item.previous != null && <span>{uiText("前期") + " "}{withUnit(item.previous, item.unit)}</span>}{" "}
                      {item.source && <span>{sourceLabels[item.source] ?? item.source}</span>}
                    </p>
                    <small>{item.published_at ? item.type === "market_consensus" ? uiText("資料發布") : uiText("官方發布") : uiText("首次取得")}{" "} {stamp(item.published_at ?? item.observed_at)}{" "}{uiText("（台灣時間）")}</small>
                    <small className="macro-identity">{uiText("指標 ID") + " "}{item.id}{item.version != null ? " " + uiText("· 版本 {{p0}}", { p0: item.version }) : ""}</small>
                    {href && <a href={href} target="_blank" rel="noreferrer">{item.type === "market_consensus" ? uiText("查看資料來源") : uiText("查看官方來源")} ↗</a>}
                  </article>
                );
              })}
            </div>
            <p className="macro-timezone">{uiText("時間以台灣時間顯示。")}</p>
          </details>
        </>
      )}
      {canEnsure && (
        <div className="macro-panel-actions">
          <button className="secondary-button" onClick={onEnsure}>
            {state.status === "unconfigured" ? uiText("重新檢查") : state.status === "failed" ? uiText("重試解讀") : result ? uiText("更新解讀") : uiText("產生解讀")}
          </button>
        </div>
      )}
    </section>
  );
}
