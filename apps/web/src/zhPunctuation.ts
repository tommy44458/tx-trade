// Model reports in Traditional Chinese often use ASCII commas and semicolons, and run Chinese straight
// into English or numbers. Shown as written, "站上阻力,動能轉強" and "結構仍為rising" read cramped; this restores
// full-width marks and a space between the scripts, for display only. The saved report is unchanged.

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

// Chinese characters only: punctuation such as 。，（）「」 takes no space.
const HAN = "[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]";
// A run of English or digits starts with a letter, a digit, or a sign before a digit (+2%), and ends with a
// letter, a digit, or the percent sign that belongs to it (5%).
const HAN_THEN_LATIN = new RegExp(`(${HAN})([A-Za-z0-9]|[+$-](?=[0-9]))`, "g");
const LATIN_THEN_HAN = new RegExp(`([A-Za-z0-9%])(${HAN})`, "g");

/** One space between Chinese and English or numbers: "結構仍為rising" becomes "結構仍為 rising". */
export function spaceBetweenScripts(text: string): string {
  return text.replace(HAN_THEN_LATIN, "$1 $2").replace(LATIN_THEN_HAN, "$1 $2");
}

/** Every string in a report, as it reads best in Traditional Chinese: full-width marks, spaced scripts. */
export function localizeReportText<T>(value: T, locale: string | null | undefined): T {
  if (locale !== "zh-TW") return value;
  const walk = (item: unknown): unknown => {
    if (typeof item === "string") return spaceBetweenScripts(fullWidthPunctuation(item));
    if (Array.isArray(item)) return item.map(walk);
    if (item && typeof item === "object") {
      return Object.fromEntries(Object.entries(item).map(([key, entry]) => [key, walk(entry)]));
    }
    return item;
  };
  return walk(value) as T;
}
