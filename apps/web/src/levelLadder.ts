type Zone = { low: string; high: string };
export type LadderRow<T extends Zone> = { kind: "level"; level: T } | { kind: "now"; price: string };

/** Zones from the highest price down, with the current price marked where it falls between them. */
export function levelLadder<T extends Zone>(levels: T[], price?: string | null): LadderRow<T>[] {
  const sorted = [...levels].sort((a, b) => Number(b.high) - Number(a.high));
  const rows: LadderRow<T>[] = sorted.map((level) => ({ kind: "level", level }));
  const current = Number(price);
  if (!price || !Number.isFinite(current)) return rows;
  // A zone the price is inside of keeps its highlight instead of a separate marker.
  if (sorted.some((level) => Number(level.low) <= current && current <= Number(level.high))) return rows;
  const below = sorted.findIndex((level) => Number(level.high) < current);
  rows.splice(below === -1 ? rows.length : below, 0, { kind: "now", price });
  return rows;
}
