// Fund flows data (the "smart money" monitor) from the cloud, and the small pure helpers its page uses.

export type Coverage = "large_only" | "exchange_and_whales" | "exchange";
export type FlowKind = "exchange_in" | "exchange_out" | "exchange_move" | "whale";
export type FlowWindow = "1d" | "7d" | "30d" | "90d";
export const FLOW_WINDOWS: FlowWindow[] = ["1d", "7d", "30d", "90d"];

export type FlowTotals = {
  inflow: number;
  outflow: number;
  inflow_usd: number;
  outflow_usd: number;
  whale_count: number;
  whale_usd: number;
  net: number;
  net_usd: number;
};

export type FlowPoint = {
  t: number;
  inflow: number;
  outflow: number;
  inflow_usd: number;
  outflow_usd: number;
};

export type FlowSeries = { step: "hour" | "day"; points: FlowPoint[] };

export type FlowEvent = {
  kind: FlowKind;
  amount: number;
  usd: number;
  from_entity: string | null;
  to_entity: string | null;
  from_address: string | null;
  to_address: string | null;
  tx: string;
  chain: "btc" | "eth";
  ts: number;
};

type SyncState = Partial<Record<"btc" | "eth", { height: number; updated_at: number }>>;

export type SmartMoneyOverview = {
  generated_at: number;
  assets: {
    asset: string;
    coverage: Coverage;
    "1d": FlowTotals;
    "7d": FlowTotals;
    series: FlowSeries;
    events: FlowEvent[];
  }[];
  stablecoins: {
    total_usd: number | null;
    change_1d: number | null;
    change_7d: number | null;
    change_30d: number | null;
    series: { t: number; total_usd: number }[];
    exchange: Record<"1d" | "7d", { inflow_usd: number; outflow_usd: number; net_usd: number }>;
  };
  sync: SyncState;
  stale?: boolean;
};

export type SmartMoneyAsset = {
  generated_at: number;
  asset: string;
  window: FlowWindow;
  coverage: Coverage;
  totals: FlowTotals;
  series: FlowSeries;
  events: FlowEvent[];
  sync: SyncState;
  stale?: boolean;
};

export type TrackedAsset = { asset: string; coverage: Coverage };

/** The coin behind a market: binance:perp:1000PEPEUSDT trades PEPE. */
export function assetForMarket(marketId: string): string {
  const symbol = marketId.split(":").pop() ?? "";
  return symbol.replace(/(USDT|USDC)$/, "").replace(/^1000+(?=[A-Z])/, "");
}

/** When the chain monitor last finished, for the freshness line; null before its first run. */
export function lastSynced(sync: SyncState): number | null {
  const times = Object.values(sync).map((s) => s?.updated_at ?? 0).filter((t) => t > 0);
  return times.length ? Math.max(...times) : null;
}

/** The monitor runs every five minutes; twenty quiet minutes means it is behind. */
export const SYNC_LATE_MS = 20 * 60_000;

export function formatUsd(value: number, locale: string, signed = false): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    notation: Math.abs(value) >= 10_000 ? "compact" : "standard",
    maximumFractionDigits: Math.abs(value) >= 10_000 ? 1 : 0,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(value);
}

export function formatAmount(value: number, locale: string): string {
  const size = Math.abs(value);
  return new Intl.NumberFormat(locale, {
    notation: size >= 100_000 ? "compact" : "standard",
    maximumFractionDigits: size >= 100_000 ? 1 : size >= 100 ? 0 : size >= 1 ? 2 : 4,
  }).format(value);
}

/** 0x1234…abcd, bc1qxy…7k2p: enough to recognise an address, short enough for a row. */
export function shortAddress(address: string | null): string {
  if (!address) return "—";
  return address.length > 14 ? `${address.slice(0, 6)}…${address.slice(-4)}` : address;
}

export function explorerUrl(event: Pick<FlowEvent, "chain" | "tx">): string {
  return event.chain === "btc"
    ? `https://mempool.space/tx/${event.tx}`
    : `https://etherscan.io/tx/${event.tx}`;
}

/** The tallest bar in a series, so bars are drawn against one scale; at least 1. */
export function seriesScale(points: FlowPoint[]): number {
  return Math.max(1, ...points.map((p) => Math.max(p.inflow_usd, p.outflow_usd)));
}

/** The frozen fund flows a follow-up conversation is about. */
export type FlowSnapshot = { id: string; asset: string; window: FlowWindow; as_of: string; created_at: string };

