import { uiText } from "./i18n/index.ts";

export type PositionSource = "manual" | "bingx" | "binance";

/** Missing source belongs to legacy manual records; unknown sources stay protected. */
export const isManualPosition = (source: string | null | undefined): boolean =>
  source === "manual" || source == null;

export function positionSourceLabel(source: string | null | undefined, contractType?: string | null): string {
  if (source === "binance") return uiText("Binance USDT 永續");
  if (source === "bingx") return contractType === "standard" ? uiText("BingX 標準合約") : uiText("BingX 永續");
  return isManualPosition(source) ? uiText("手動登記") : uiText("交易所匯入");
}

export function liquidationSourceLabel(source: string | null | undefined): string {
  if (source === "binance") return uiText("Binance 強平價");
  if (source === "bingx") return uiText("BingX 強平價");
  return isManualPosition(source) ? uiText("手動登記強平價") : uiText("交易所強平價");
}

export function importedPositionFacts<T extends {
  market_id: string; side: "long" | "short"; leverage: number; margin_mode: "isolated" | "cross";
  entry_price: string; quantity: string; entry_time?: string | null; exchange_liquidation_price?: string | null;
}>(position: T) {
  return {
    market_id: position.market_id, side: position.side, leverage: position.leverage,
    margin_mode: position.margin_mode, entry_price: position.entry_price, quantity: position.quantity,
    entry_time: position.entry_time ?? null, exchange_liquidation_price: position.exchange_liquidation_price ?? null,
  };
}
