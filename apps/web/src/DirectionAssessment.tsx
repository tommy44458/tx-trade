import { uiText } from "./i18n/index.ts";
import "./DirectionAssessment.css";

type Side = "long" | "short";
type Verdict = { verdict: "reasonable" | "conditional" | "unsuitable"; reason: string };
export type DirectionAssessmentData = Partial<Record<Side, Verdict>> | null;

const verdictLabel = (value: Verdict["verdict"]) => value === "reasonable" ? uiText("現在合理")
  : value === "conditional" ? uiText("有條件") : uiText("現在不適合");

const sideLabel = (side: Side) => side === "long" ? uiText("做多") : uiText("做空");

/** The model judges both directions without seeing the user's hypothesis. */
export default function DirectionAssessment({ assessment, bias }: {
  assessment?: DirectionAssessmentData;
  bias?: string | null;
}) {
  if (!assessment) return null;
  const mine: Side | null = bias === "bullish" ? "long" : bias === "bearish" ? "short" : null;
  // The user's own direction comes first so a mismatch is the first thing read.
  const sides = (["long", "short"] as const).filter((side) => side === mine || assessment[side])
    .sort((a, b) => Number(b === mine) - Number(a === mine));
  return (
    <section className="panel direction-assessment" aria-label={uiText("多空方向評估")}>
      <header className="ai-report-header">
        <div><span>{uiText("多空方向評估")}</span></div>
        <small className="direction-assessment-note">{uiText("AI 看不到你的方向看法")}</small>
      </header>
      <ul className="direction-list">
        {sides.map((side) => {
          const item = assessment[side];
          return (
            <li key={side} className={`direction-row${side === mine && item?.verdict === "unsuitable" ? " flagged" : ""}`}>
              <div className="direction-row-head">
                <span className={`stance-pill ${side}`}>{sideLabel(side)}</span>
                {item && <span className={`verdict-pill ${item.verdict}`}>{verdictLabel(item.verdict)}</span>}
                {side === mine && <span className="tag">{uiText("你的看法")}</span>}
              </div>
              <p>{item ? item.reason : uiText("AI 本次未回傳此方向的評估。")}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
