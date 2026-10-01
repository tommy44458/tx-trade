import { uiText } from "./i18n/index.ts";
export type ChartCandle = {
  open_time: string | number;
  close_time: string | number;
  open: string | number;
  high: string | number;
  low: string | number;
  close: string | number;
  volume?: string | number;
  closed?: boolean;
};

export type ChartLevel = {
  id?: string;
  kind: string;
  low: string | number;
  high: string | number;
  zone_state?: string;
  price_relation?: "below" | "inside" | "above";
};

export type NormalizedCandle = {
  time: number;
  closeTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  closed?: boolean;
};

export type ChartZone = {
  id: string;
  kind: "support" | "resistance";
  low: number;
  high: number;
  inside: boolean;
  state?: string;
};

function timestamp(value: string | number): number {
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(numeric) && value !== "")
    return Math.floor(numeric > 10_000_000_000 ? numeric / 1000 : numeric);
  return Math.floor(Date.parse(String(value)) / 1000);
}

export function normalizeCandles(candles: ChartCandle[]): NormalizedCandle[] {
  const byTime = new Map<number, NormalizedCandle>();
  for (const input of candles) {
    const candle: NormalizedCandle = {
      time: timestamp(input.open_time),
      closeTime: timestamp(input.close_time),
      open: Number(input.open),
      high: Number(input.high),
      low: Number(input.low),
      close: Number(input.close),
      closed: input.closed,
    };
    if (
      !Number.isFinite(candle.time) ||
      ![candle.open, candle.high, candle.low, candle.close].every(
        (value) => Number.isFinite(value) && value > 0,
      ) ||
      candle.low > Math.min(candle.open, candle.close) ||
      candle.high < Math.max(candle.open, candle.close)
    )
      continue;
    const volume = Number(input.volume);
    if (input.volume !== undefined && Number.isFinite(volume) && volume >= 0)
      candle.volume = volume;
    byTime.set(candle.time, candle);
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

export function currentChartPrice(
  quotePrice: string | number | undefined,
  candles: NormalizedCandle[],
): number | undefined {
  const quote = Number(quotePrice);
  return Number.isFinite(quote) && quote > 0 ? quote : candles.at(-1)?.close;
}

export function chartZones(levels: ChartLevel[], price?: number): ChartZone[] {
  const byId = new Map<string, ChartZone>();
  for (const level of levels) {
    const low = Number(level.low);
    const high = Number(level.high);
    if (
      (level.kind !== "support" && level.kind !== "resistance") ||
      !Number.isFinite(low) ||
      !Number.isFinite(high) ||
      low <= 0 ||
      high < low
    )
      continue;
    const id = level.id ?? `${level.kind}:${low}:${high}`;
    byId.set(id, {
      id,
      kind: level.kind,
      low,
      high,
      // The live quote wins over a stale snapshot relation. Crossing the center
      // or moving within the band never hides the original price zone.
      inside: price !== undefined && price >= low && price <= high,
      state: level.zone_state,
    });
  }
  return [...byId.values()];
}

export function nearestChartZones(zones: ChartZone[], price?: number): ChartZone[] {
  if (price === undefined) return zones;
  const distance = (zone: ChartZone) =>
    Math.max(zone.low - price, price - zone.high, 0);
  const selected = new Map<string, ChartZone>();
  for (const kind of ["support", "resistance"] as const) {
    for (const zone of zones
      .filter((candidate) => candidate.kind === kind && !isInvalidatedZone(candidate))
      .sort((a, b) => distance(a) - distance(b))
      .slice(0, 3))
      selected.set(zone.id, zone);
    const recentInvalidated = zones
      .filter((candidate) => candidate.kind === kind && isInvalidatedZone(candidate))
      .sort((a, b) => distance(a) - distance(b))[0];
    if (recentInvalidated) selected.set(recentInvalidated.id, recentInvalidated);
  }
  // Even overlapping inside zones survive the proximity limit.
  for (const zone of zones) if (zone.inside) selected.set(zone.id, zone);
  return [...selected.values()];
}

export function zoneTitle(zone: ChartZone): string {
  return zone.kind === "support" ? uiText("支撐") : uiText("壓力");
}

export function isInvalidatedZone(zone: ChartZone): boolean {
  return zone.state === "invalidated";
}

export function chartPrice(value: number): string {
  const precision = value >= 1000 ? 2 : value >= 1 ? 4 : value >= 0.01 ? 6 : 8;
  return value.toLocaleString("en-US", { maximumFractionDigits: precision });
}

export function chartRefreshOffset(
  previous: NormalizedCandle[],
  next: NormalizedCandle[],
): number {
  const nextIndex = new Map(next.map((candle, index) => [candle.time, index]));
  for (let index = 0; index < previous.length; index++) {
    const matching = nextIndex.get(previous[index].time);
    if (matching !== undefined) return matching - index;
  }
  return 0;
}
