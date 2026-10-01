import { uiText } from "./i18n/index.ts";
export const MIN_LEVERAGE = 1;
export const MAX_LEVERAGE = 125;
export const LEVERAGE_PRESETS = [1, 2, 3, 5, 10, 20, 30, 50, 75, 100, 125] as const;

/** Keep editing text separate from the last valid, saved integer. */
export function validateLeverageInput(
  text: string,
  min = MIN_LEVERAGE,
  max = MAX_LEVERAGE,
): { value: number | null; error: string | null } {
  const trimmed = text.trim();
  if (!trimmed) return { value: null, error: uiText("請輸入槓桿倍數。") };
  if (!/^\d+$/.test(trimmed)) {
    return { value: null, error: uiText("請輸入 {{p0}}–{{p1}} 倍的整數。", { p0: min, p1: max }) };
  }
  const value = Number(trimmed);
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    return { value: null, error: uiText("槓桿必須介於 {{p0}}–{{p1}} 倍。", { p0: min, p1: max }) };
  }
  return { value, error: null };
}
