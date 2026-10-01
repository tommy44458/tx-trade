import { isAnalysisTimeframe } from "./timeframes.ts";

export type SavedPositionAnalysis = {
  id: string;
  status: string;
  submitted_input: { kind?: string; market_id: string; timeframe: string };
  report: { market_id: string; timeframe: string } | null;
};

/** A slower polling response cannot reopen a task that already completed or was replaced. */
export function applyPendingAnalysisUpdate<T extends { id: string; status: string }>(current: T | null, updated: T): T | null {
  return current?.id === updated.id && ["queued", "running"].includes(current.status) ? updated : current;
}

/** A saved report must belong to this exact pair and its original supported timeframe. */
export function isLatestPositionAnalysis(
  value: unknown,
  marketId: string,
): value is SavedPositionAnalysis {
  if (!value || typeof value !== "object") return false;
  const item = value as SavedPositionAnalysis;
  return typeof item.id === "string" && !!item.id && item.status === "completed" &&
    item.submitted_input?.kind === "positions" && item.submitted_input.market_id === marketId &&
    isAnalysisTimeframe(item.submitted_input.timeframe) &&
    item.report?.market_id === marketId && item.report.timeframe === item.submitted_input.timeframe;
}

/** Cancellation also protects against transports that finish after AbortSignal was ignored. */
export function createPositionAnalysisLookup<T>() {
  let revision = 0;
  let controller: AbortController | null = null;
  function invalidate() {
    revision += 1;
    controller?.abort();
    controller = null;
  }
  return {
    invalidate,
    async read(
      request: (signal: AbortSignal) => Promise<T>,
      onLoaded: (value: T) => void,
      onError: (reason: unknown) => void,
    ) {
      invalidate();
      const currentRevision = revision;
      const current = new AbortController();
      controller = current;
      try {
        const result = await request(current.signal);
        if (revision === currentRevision && !current.signal.aborted) onLoaded(result);
      } catch (reason) {
        if (revision === currentRevision && !current.signal.aborted) onError(reason);
      } finally {
        if (controller === current) controller = null;
      }
    },
  };
}
