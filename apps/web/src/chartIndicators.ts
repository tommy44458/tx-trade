/**
 * Chart series for the indicators an analysis report used, with the same
 * formulas and frozen parameters as the backend (apps/api indicators.py and
 * optional_indicators.py). Seeds start at the first charted candle, so early
 * values can differ slightly from the report until the smoothing converges.
 */
import type { NormalizedCandle } from "./candlestickData";

export type IndicatorKey = "ema" | "vwap" | "bollinger" | "keltner" | "donchian" | "fibonacci" | "swing"
  | "rsi" | "macd" | "atr" | "obv" | "adx" | "stochastic";
export type IndicatorSpec = {
  key: IndicatorKey;
  params: Record<string, number>;
  /** Oscillators use their own pane below the price chart. */
  pane: boolean;
  defaultOn: boolean;
  fibLevels?: { ratio: string; price: number }[];
};
export type Point = { time: number; value: number };
export type Line = { id: string; points: Point[] };

type Frame = { indicators?: Record<string, Record<string, unknown> | undefined> };
export type IndicatorReport = {
  timeframe: string;
  technical_snapshot?: {
    timeframes?: Record<string, Frame | undefined>;
    initial_indicator_selection?: { names?: string[]; parameters?: Record<string, Record<string, unknown>> };
  } | null;
  tool_trace?: { tool: string; result?: Record<string, unknown> }[];
};

const OPTIONAL: Record<string, { key: IndicatorKey; pane: boolean; defaults: Record<string, number> }> = {
  bollinger: { key: "bollinger", pane: false, defaults: { period: 20, multiplier: 2 } },
  keltner: { key: "keltner", pane: false, defaults: { period: 20, atr_period: 14, multiplier: 2 } },
  donchian: { key: "donchian", pane: false, defaults: { period: 20 } },
  fibonacci: { key: "fibonacci", pane: false, defaults: {} },
  obv: { key: "obv", pane: true, defaults: { period: 20 } },
  adx_dmi: { key: "adx", pane: true, defaults: { period: 14, adx_period: 14 } },
  stochastic: { key: "stochastic", pane: true, defaults: { period: 14, smooth_k: 3, smooth_d: 3 } },
};
const ORDER: IndicatorKey[] = ["ema", "vwap", "bollinger", "keltner", "donchian", "fibonacci", "swing",
  "rsi", "macd", "atr", "obv", "adx", "stochastic"];

function numbers(values: Record<string, unknown> | undefined, defaults: Record<string, number>) {
  const result = { ...defaults };
  for (const [name, value] of Object.entries(values ?? {})) {
    const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
    if (Number.isFinite(parsed)) result[name] = parsed;
  }
  return result;
}

/** Indicators the report actually computed for its primary timeframe. */
export function reportIndicators(report: IndicatorReport | null | undefined): IndicatorSpec[] {
  const indicators = report?.technical_snapshot?.timeframes?.[report.timeframe]?.indicators;
  if (!report || !indicators) return [];
  const usable = (name: string) => {
    const value = indicators[name];
    return value !== undefined && value.status !== "unavailable";
  };
  const specs = new Map<IndicatorKey, IndicatorSpec>();
  if (usable("trend_ema")) specs.set("ema", { key: "ema", params: { fast: 20, slow: 50 }, pane: false, defaultOn: true });
  if (usable("rolling_vwap")) specs.set("vwap", { key: "vwap", params: numbers(indicators.rolling_vwap, { period: 20 }), pane: false, defaultOn: false });
  if (usable("swing_points")) specs.set("swing", { key: "swing", params: numbers(indicators.swing_points, { width: 3 }), pane: false, defaultOn: false });
  if (usable("rsi")) specs.set("rsi", { key: "rsi", params: numbers(indicators.rsi, { period: 14 }), pane: true, defaultOn: false });
  if (usable("macd")) specs.set("macd", { key: "macd", params: { fast: 12, slow: 26, signal: 9 }, pane: true, defaultOn: false });
  if (usable("volatility_atr")) specs.set("atr", { key: "atr", params: numbers(indicators.volatility_atr, { period: 14 }), pane: true, defaultOn: false });
  const selection = report.technical_snapshot?.initial_indicator_selection;
  // Optional tools the model requested itself for this timeframe are also part of what it used.
  const requested = new Map((report.tool_trace ?? [])
    .filter((run) => run.tool in OPTIONAL && run.result?.timeframe === report.timeframe && run.result.status !== "unavailable")
    .map((run) => [run.tool, run.result!]));
  for (const name of Object.keys(OPTIONAL)) {
    const definition = OPTIONAL[name];
    const result = usable(name) ? indicators[name] : requested.get(name);
    if (!result) continue;
    const spec: IndicatorSpec = { key: definition.key, pane: definition.pane, defaultOn: !!selection?.names?.includes(name),
      params: numbers((result.parameters as Record<string, unknown> | undefined) ?? selection?.parameters?.[name], definition.defaults) };
    if (name === "fibonacci") {
      const retracements = (result.retracements ?? {}) as Record<string, { price?: string; status?: string }>;
      spec.fibLevels = Object.entries(retracements)
        .filter(([, level]) => level.status === "available" && Number.isFinite(Number(level.price)))
        .map(([ratio, level]) => ({ ratio, price: Number(level.price) }));
      if (!spec.fibLevels.length) continue;
    }
    specs.set(definition.key, spec);
  }
  return ORDER.filter((key) => specs.has(key)).map((key) => specs.get(key)!);
}

const line = (candles: NormalizedCandle[], values: (number | null)[]): Point[] =>
  values.flatMap((value, index) => value === null || !Number.isFinite(value) ? [] : [{ time: candles[index].time, value }]);

function ema(values: number[], period: number, start = 0): (number | null)[] {
  const result: (number | null)[] = values.map(() => null);
  if (values.length - start < period) return result;
  let current = values.slice(start, start + period).reduce((sum, value) => sum + value, 0) / period;
  result[start + period - 1] = current;
  const alpha = 2 / (period + 1);
  for (let index = start + period; index < values.length; index += 1) {
    current = alpha * values[index] + (1 - alpha) * current;
    result[index] = current;
  }
  return result;
}

function rolling(values: number[], period: number, reduce: (window: number[]) => number): (number | null)[] {
  return values.map((_, index) => index + 1 < period ? null : reduce(values.slice(index + 1 - period, index + 1)));
}

const mean = (window: number[]) => window.reduce((sum, value) => sum + value, 0) / window.length;

function trueRanges(candles: NormalizedCandle[]): (number | null)[] {
  return candles.map((candle, index) => index === 0 ? null : Math.max(candle.high - candle.low,
    Math.abs(candle.high - candles[index - 1].close), Math.abs(candle.low - candles[index - 1].close)));
}

/** Wilder RMA seeded by the mean of the first `period` values from `offset`. */
function wilder(values: (number | null)[], period: number, offset: number): (number | null)[] {
  const result: (number | null)[] = values.map(() => null);
  if (values.length - offset < period) return result;
  let current = mean(values.slice(offset, offset + period) as number[]);
  result[offset + period - 1] = current;
  for (let index = offset + period; index < values.length; index += 1) {
    current = (current * (period - 1) + (values[index] as number)) / period;
    result[index] = current;
  }
  return result;
}

export function indicatorLines(spec: IndicatorSpec, candles: NormalizedCandle[]): Line[] {
  const closes = candles.map((candle) => candle.close);
  const p = spec.params;
  switch (spec.key) {
    case "ema":
      return [{ id: "ema20", points: line(candles, ema(closes, p.fast)) },
        { id: "ema50", points: line(candles, ema(closes, p.slow)) }];
    case "vwap": {
      const typical = candles.map((candle) => (candle.high + candle.low + candle.close) / 3 * (candle.volume ?? 0));
      const volume = candles.map((candle) => candle.volume ?? 0);
      return [{ id: "vwap", points: line(candles, candles.map((_, index) => {
        if (index + 1 < p.period) return null;
        const total = volume.slice(index + 1 - p.period, index + 1).reduce((sum, value) => sum + value, 0);
        return total > 0 ? typical.slice(index + 1 - p.period, index + 1).reduce((sum, value) => sum + value, 0) / total : null;
      })) }];
    }
    case "bollinger": {
      const middle = rolling(closes, p.period, mean);
      const deviation = rolling(closes, p.period, (window) => {
        const average = mean(window);
        return Math.sqrt(window.reduce((sum, value) => sum + (value - average) ** 2, 0) / window.length);
      });
      return [{ id: "upper", points: line(candles, middle.map((m, i) => m === null ? null : m + p.multiplier * deviation[i]!)) },
        { id: "middle", points: line(candles, middle) },
        { id: "lower", points: line(candles, middle.map((m, i) => m === null ? null : m - p.multiplier * deviation[i]!)) }];
    }
    case "keltner": {
      const middle = ema(closes, p.period);
      const atr = wilder(trueRanges(candles), p.atr_period, 1);
      const band = (sign: number) => middle.map((m, i) => m === null || atr[i] === null ? null : m + sign * p.multiplier * atr[i]!);
      return [{ id: "upper", points: line(candles, band(1)) }, { id: "middle", points: line(candles, middle) },
        { id: "lower", points: line(candles, band(-1)) }];
    }
    case "donchian": {
      const upper = rolling(candles.map((candle) => candle.high), p.period, (window) => Math.max(...window));
      const lower = rolling(candles.map((candle) => candle.low), p.period, (window) => Math.min(...window));
      return [{ id: "upper", points: line(candles, upper) }, { id: "lower", points: line(candles, lower) }];
    }
    case "rsi": {
      const changes = closes.map((close, index) => index === 0 ? null : close - closes[index - 1]);
      const gains = wilder(changes.map((change) => change === null ? null : Math.max(change, 0)), p.period, 1);
      const losses = wilder(changes.map((change) => change === null ? null : Math.max(-change, 0)), p.period, 1);
      return [{ id: "rsi", points: line(candles, gains.map((gain, i) => {
        const loss = losses[i];
        if (gain === null || loss === null) return null;
        return gain === 0 && loss === 0 ? 50 : loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
      })) }];
    }
    case "macd": {
      const fast = ema(closes, p.fast);
      const slow = ema(closes, p.slow);
      const macd = fast.map((value, i) => value === null || slow[i] === null ? null : value - slow[i]!);
      const firstIndex = macd.findIndex((value) => value !== null);
      const signal = firstIndex < 0 ? macd.map(() => null) : ema(macd.map((value) => value ?? 0), p.signal, firstIndex);
      return [{ id: "macd", points: line(candles, macd) },
        { id: "signal", points: line(candles, signal) },
        { id: "histogram", points: line(candles, macd.map((value, i) => value === null || signal[i] === null ? null : value - signal[i]!)) }];
    }
    case "atr":
      return [{ id: "atr", points: line(candles, wilder(trueRanges(candles), p.period, 1)) }];
    case "obv": {
      let total = 0;
      return [{ id: "obv", points: line(candles, candles.map((candle, index) => {
        if (index > 0) {
          const volume = candle.volume ?? 0;
          total += candle.close > candles[index - 1].close ? volume : candle.close < candles[index - 1].close ? -volume : 0;
        }
        return total;
      })) }];
    }
    case "adx": {
      const moves = candles.map((candle, index) => {
        if (index === 0) return null;
        const up = candle.high - candles[index - 1].high;
        const down = candles[index - 1].low - candle.low;
        return [up > down && up > 0 ? up : 0, down > up && down > 0 ? down : 0];
      });
      const tr = wilder(trueRanges(candles), p.period, 1);
      const plus = wilder(moves.map((move) => move?.[0] ?? null), p.period, 1);
      const minus = wilder(moves.map((move) => move?.[1] ?? null), p.period, 1);
      const plusDi = tr.map((value, i) => value === null ? null : value ? 100 * plus[i]! / value : 0);
      const minusDi = tr.map((value, i) => value === null ? null : value ? 100 * minus[i]! / value : 0);
      const dx = plusDi.map((value, i) => {
        if (value === null || minusDi[i] === null) return null;
        const sum = value + minusDi[i]!;
        return sum ? 100 * Math.abs(value - minusDi[i]!) / sum : 0;
      });
      const start = dx.findIndex((value) => value !== null);
      const adx = start < 0 ? dx.map(() => null) : wilder(dx, p.adx_period, start);
      return [{ id: "adx", points: line(candles, adx) }, { id: "plus", points: line(candles, plusDi) },
        { id: "minus", points: line(candles, minusDi) }];
    }
    case "stochastic": {
      const raw = candles.map((candle, index) => {
        if (index + 1 < p.period) return null;
        const window = candles.slice(index + 1 - p.period, index + 1);
        const low = Math.min(...window.map((item) => item.low));
        const high = Math.max(...window.map((item) => item.high));
        return high === low ? null : 100 * (candle.close - low) / (high - low);
      });
      const smooth = (values: (number | null)[], period: number) => values.map((_, index) => {
        const window = values.slice(index + 1 - period, index + 1);
        return index + 1 < period || window.some((value) => value === null) ? null : mean(window as number[]);
      });
      const k = smooth(raw, p.smooth_k);
      return [{ id: "k", points: line(candles, k) }, { id: "d", points: line(candles, smooth(k, p.smooth_d)) }];
    }
    default:
      return [];
  }
}

/** Confirmed swing highs and lows, as in the backend swing_points tool. */
export function swingMarkers(candles: NormalizedCandle[], width: number) {
  const markers: { time: number; kind: "high" | "low"; price: number }[] = [];
  for (let index = width; index < candles.length - width; index += 1) {
    const window = candles.slice(index - width, index + width + 1);
    if (candles[index].low === Math.min(...window.map((candle) => candle.low)))
      markers.push({ time: candles[index].time, kind: "low", price: candles[index].low });
    if (candles[index].high === Math.max(...window.map((candle) => candle.high)))
      markers.push({ time: candles[index].time, kind: "high", price: candles[index].high });
  }
  return markers;
}
