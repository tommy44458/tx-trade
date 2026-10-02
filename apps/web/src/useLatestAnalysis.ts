import { useEffect, useMemo, useRef, useState } from "react";
import { uiText } from "./i18n/index.ts";
import {
  createPositionAnalysisLookup, isLatestAnalysis, type SavedAnalysisKind, type SavedPositionAnalysis,
} from "./latestPositionAnalysis.ts";
import { apiFetch } from "./transport.ts";

type Scope = { active: boolean; marketId: string };
type Result<T> = { scope: Scope; refreshVersion: number; status: "loaded" | "empty" | "error"; data: T | null; error: string };

function readFailed(kind: SavedAnalysisKind, status: number) {
  return kind === "market"
    ? uiText("最新市場分析讀取失敗（{{p0}}）", { p0: status })
    : uiText("最新持倉分析讀取失敗（{{p0}}）", { p0: status });
}

function incomplete(kind: SavedAnalysisKind) {
  return kind === "market"
    ? uiText("最新市場分析資料不完整，請重試。")
    : uiText("最新持倉分析資料不完整，請重試。");
}

/** Read a saved report only; this hook never creates an analysis or changes its preferences. */
export default function useLatestAnalysis<T extends SavedPositionAnalysis>(
  kind: SavedAnalysisKind,
  active: boolean,
  marketId: string,
  onLoaded: (value: T) => void,
) {
  const [result, setResult] = useState<Result<T> | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  // A new activation has a new identity even for the same pair; old results cannot flash on entry.
  const scope = useMemo(() => ({ active, marketId }), [active, marketId]);
  const lookup = useRef(createPositionAnalysisLookup<T | null>());
  const loadedCallback = useRef(onLoaded);
  loadedCallback.current = onLoaded;

  useEffect(() => {
    if (!active || !marketId) return;
    setResult(null);
    void lookup.current.read(async (signal) => {
      const response = await apiFetch(`/api/v1/analyses/latest?kind=${kind}&market_id=${encodeURIComponent(marketId)}`, { signal });
      if (!response.ok) throw new Error(readFailed(kind, response.status));
      let body: unknown;
      try { body = await response.json(); }
      catch { throw new Error(incomplete(kind)); }
      if (body === null) return null;
      if (!isLatestAnalysis(body, marketId, kind)) throw new Error(incomplete(kind));
      return body as T;
    }, (data) => {
      if (data) loadedCallback.current(data);
      setResult({ scope, refreshVersion, status: data ? "loaded" : "empty", data, error: "" });
    }, (reason) => {
      setResult({ scope, refreshVersion, status: "error", data: null, error: (reason as Error).message });
    });
    return () => lookup.current.invalidate();
  }, [kind, active, marketId, scope, refreshVersion]);

  const current = active && result?.scope === scope && result.refreshVersion === refreshVersion ? result : null;
  return {
    data: current?.data ?? null,
    status: !active || !marketId ? "idle" : current?.status ?? "loading",
    error: current?.error ?? "",
    invalidate: lookup.current.invalidate,
    refresh: () => {
      lookup.current.invalidate();
      setResult(null);
      setRefreshVersion((value) => value + 1);
    },
  };
}
