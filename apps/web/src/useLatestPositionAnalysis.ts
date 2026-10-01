import { useEffect, useMemo, useRef, useState } from "react";
import { uiText } from "./i18n/index.ts";
import {
  createPositionAnalysisLookup, isLatestPositionAnalysis, type SavedPositionAnalysis,
} from "./latestPositionAnalysis.ts";

type Scope = { active: boolean; marketId: string };
type Result<T> = { scope: Scope; refreshVersion: number; status: "loaded" | "empty" | "error"; data: T | null; error: string };

/** Read a saved report only; this hook never creates an analysis or changes its preferences. */
export default function useLatestPositionAnalysis<T extends SavedPositionAnalysis>(
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
      const response = await fetch(`/api/v1/analyses/latest?kind=positions&market_id=${encodeURIComponent(marketId)}`, { signal });
      if (!response.ok) throw new Error(uiText("最新持倉分析讀取失敗（{{p0}}）", { p0: response.status }));
      let body: unknown;
      try { body = await response.json(); }
      catch { throw new Error(uiText("最新持倉分析資料不完整，請重試。")); }
      if (body === null) return null;
      if (!isLatestPositionAnalysis(body, marketId)) throw new Error(uiText("最新持倉分析資料不完整，請重試。"));
      return body as T;
    }, (data) => {
      if (data) loadedCallback.current(data);
      setResult({ scope, refreshVersion, status: data ? "loaded" : "empty", data, error: "" });
    }, (reason) => {
      setResult({ scope, refreshVersion, status: "error", data: null, error: (reason as Error).message });
    });
    return () => lookup.current.invalidate();
  }, [active, marketId, scope, refreshVersion]);

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
