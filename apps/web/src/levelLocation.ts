import { uiText } from "./i18n/index.ts";
export type LevelLocation = {
  kind: string;
  price_relation?: "below" | "inside" | "above";
  last_closed_relation?: "below" | "inside" | "above";
  consecutive_closes_beyond?: number;
};

export function levelLocationLabel(level: LevelLocation): string {
  if (!level.price_relation) return "";
  const position =
    level.price_relation === "inside"
      ? uiText("現價在區間內")
      : level.price_relation === "above"
        ? uiText("現價在區間上方")
        : uiText("現價在區間下方");
  if (level.consecutive_closes_beyond === 1)
    return uiText("{{p0}} · 僅一根收盤穿越，原區間尚未失效", { p0: position });
  const beyond = level.kind === "resistance" ? "above" : "below";
  if (level.price_relation === beyond)
    return uiText("{{p0}} · 現價穿越，尚無收盤穿越確認", { p0: position });
  if (level.last_closed_relation === "inside")
    return uiText("{{p0}} · 最新收盤仍在區間內", { p0: position });
  return position;
}
