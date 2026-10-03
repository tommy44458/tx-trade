// Model reports in Traditional Chinese often use ASCII commas and semicolons. Shown as written,
// "站上阻力,動能轉強" reads cramped; this restores full-width marks for display only. The saved report is unchanged.

const CJK = /[　-〿㐀-䶿一-鿿豈-﫿＀-￯]/;
const DIGIT = /[0-9]/;

const FULL_WIDTH: Record<string, string> = { ",": "，", ";": "；" };

/**
 * Full-width commas and semicolons in Chinese prose. A mark stays ASCII between two digits (8,500) and
 * between non-Chinese text (EMA20, EMA50); spaces around a replaced mark are dropped, as Chinese uses none.
 */
export function fullWidthPunctuation(text: string): string {
  if (!text.includes(",") && !text.includes(";")) return text;
  return text.replace(/(\s*)([,;])(\s*)/g, (match, before: string, mark: string, after: string, offset: number) => {
    const previous = text[offset - 1] ?? "";
    const next = text[offset + match.length] ?? "";
    if (!before && !after && DIGIT.test(previous) && DIGIT.test(next)) return match;
    return CJK.test(previous) || CJK.test(next) ? FULL_WIDTH[mark] : match;
  });
}

/** Every string in a report, with full-width commas and semicolons when the report is in Traditional Chinese. */
export function localizeReportText<T>(value: T, locale: string | null | undefined): T {
  if (locale !== "zh-TW") return value;
  const walk = (item: unknown): unknown => {
    if (typeof item === "string") return fullWidthPunctuation(item);
    if (Array.isArray(item)) return item.map(walk);
    if (item && typeof item === "object") {
      return Object.fromEntries(Object.entries(item).map(([key, entry]) => [key, walk(entry)]));
    }
    return item;
  };
  return walk(value) as T;
}
