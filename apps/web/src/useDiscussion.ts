import { uiText, type UiLocale } from "./i18n/index.ts";
import { useCallback, useEffect, useRef, useState } from "react";
import type { DiscussionLiveMarket } from "./discussionLiveMarket.ts";
import { apiFetch, isRemoteMode } from "./transport.ts";

export type DiscussionSubjectType = "analysis" | "macro";
export type DiscussionMessage = {
  id: string;
  sequence: number;
  role: "user" | "assistant";
  content: string;
  status: "queued" | "running" | "completed" | "failed";
  created_at: string;
  completed_at?: string | null;
  request_id?: string | null;
  provider?: string | null;
  model?: string | null;
  error: { code: string; message: string; retryable: boolean } | null;
  live_market?: DiscussionLiveMarket | null;
};
export type DiscussionState = {
  subject: {
    type: DiscussionSubjectType;
    id: string;
    title: string;
    as_of: string;
    kind?: string;
    market_id?: string;
    timeframe?: string;
    output_locale?: UiLocale;
    response_locale?: UiLocale;
  };
  session: { id: string; created_at: string; updated_at: string; output_locale?: UiLocale; response_locale?: UiLocale } | null;
  messages: DiscussionMessage[];
  busy: boolean;
  has_more: boolean;
  before_sequence: number | null;
};

type UncertainSend = { message: string; request_id: string; output_locale?: UiLocale };
type Scope = {
  key: string;
  active: boolean;
  revision: number;
  actionRunning: boolean;
  olderRunning: boolean;
  controllers: Set<AbortController>;
  reads: Set<AbortController>;
  streamRevision: number;
};

// Only drafts and uncertain submissions stay in memory. Reopening always reads SQLite.
const drafts = new Map<string, string>();
const uncertainSends = new Map<string, UncertainSend>();

class DiscussionRequestError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request(path: string, options: RequestInit): Promise<DiscussionState> {
  const response = await apiFetch(`/api/v1/discussions/${path}`, options);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new DiscussionRequestError(typeof body?.detail === "string" ? body.detail
      : body?.detail?.message ?? uiText("討論請求失敗 ({{p0}})", { p0: response.status }), response.status);
  }
  if (!body) throw new Error(uiText("未收到討論內容，請重新讀取。"));
  return body;
}

function mergeMessages(earlier: DiscussionMessage[], latest: DiscussionMessage[]) {
  const messages = new Map(earlier.map((item) => [item.id, item]));
  for (const item of latest) messages.set(item.id, item);
  return [...messages.values()].sort((a, b) => a.sequence - b.sequence);
}

export default function useDiscussion(type: DiscussionSubjectType, id: string, outputLocale: UiLocale = "zh-TW") {
  const key = `${type}:${id}`;
  const path = `${type}/${encodeURIComponent(id)}`;
  const [result, setResult] = useState<{ key: string; state: DiscussionState } | null>(null);
  const [draftState, setDraftState] = useState({ key, text: drafts.get(key) ?? "" });
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [readError, setReadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [olderError, setOlderError] = useState("");
  const [uncertain, setUncertain] = useState<UncertainSend | null>(uncertainSends.get(key) ?? null);
  const [streamFallback, setStreamFallback] = useState(false);
  const stateRef = useRef<DiscussionState | null>(null);
  const scopeRef = useRef<Scope | null>(null);
  const data = result?.key === key ? result.state : null;
  const draft = draftState.key === key ? draftState.text : drafts.get(key) ?? "";

  const setDraft = useCallback((text: string) => {
    drafts.set(key, text);
    setDraftState({ key, text });
  }, [key]);

  const current = useCallback((scope: Scope, controller: AbortController) => {
    return scope.active && scopeRef.current === scope && !controller.signal.aborted;
  }, []);

  const accept = useCallback((scope: Scope, latest: DiscussionState, older = false) => {
    if (latest.subject.type !== type || latest.subject.id !== id) return;
    const previous = stateRef.current;
    const keepEarlierCursor = !older && !!previous?.messages.length &&
      !!latest.messages.length && previous.messages[0].sequence < latest.messages[0].sequence;
    const merged = mergeMessages(previous?.messages ?? [], latest.messages);
    const next = {
      ...latest,
      messages: merged,
      // A history page has no live tail. Keep receiving the terminal streamed message.
      busy: latest.busy || (older && merged.some((message) => ["queued", "running"].includes(message.status))),
      has_more: keepEarlierCursor ? previous.has_more : latest.has_more,
      before_sequence: keepEarlierCursor ? previous.before_sequence : latest.before_sequence,
    };
    // An older page carries current server busy/session state but its own history cursor.
    stateRef.current = next;
    setResult({ key: scope.key, state: next });
    const pending = uncertainSends.get(scope.key);
    if (pending && next.messages.some((item) => item.request_id === pending.request_id)) {
      uncertainSends.delete(scope.key);
      setUncertain(null);
      if ((drafts.get(scope.key) ?? "").trim() === pending.message) setDraft("");
      setActionError("");
    }
  }, [type, id, setDraft]);

  const load = useCallback(async (scope = scopeRef.current) => {
    if (!scope?.active || scope.actionRunning || scope.olderRunning) return;
    for (const previous of scope.reads) previous.abort();
    const controller = new AbortController();
    scope.controllers.add(controller);
    scope.reads.add(controller);
    const revision = scope.revision;
    const streamRevision = scope.streamRevision;
    try {
      const latest = await request(`${path}?limit=50`, { signal: controller.signal });
      if (!current(scope, controller) || revision !== scope.revision || streamRevision !== scope.streamRevision) return;
      accept(scope, latest);
      setReadError("");
      if (!uncertainSends.has(scope.key)) setActionError("");
    } catch (reason) {
      if (current(scope, controller)) setReadError((reason as Error).message);
    } finally {
      scope.controllers.delete(controller);
      scope.reads.delete(controller);
      if (current(scope, controller)) setLoading(false);
    }
  }, [path, current, accept]);

  useEffect(() => {
    const scope: Scope = {
      key, active: true, revision: 0, actionRunning: false, olderRunning: false,
      controllers: new Set(), reads: new Set(), streamRevision: 0,
    };
    scopeRef.current = scope;
    stateRef.current = null;
    void Promise.resolve().then(() => { setStreamFallback(isRemoteMode()); return load(scope); });
    return () => {
      scope.active = false;
      for (const controller of scope.controllers) controller.abort();
    };
  }, [key, load]);

  useEffect(() => {
    const scope = scopeRef.current;
    if (!scope?.active || !data?.busy || posting || streamFallback) return;
    const revision = scope.revision;
    let source: EventSource | undefined;
    let pending: DiscussionState | null = null;
    let timer: number | undefined;
    let lastPaint = 0;
    const active = () => scope.active && scopeRef.current === scope && revision === scope.revision;
    const flush = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      if (!pending || !active()) return;
      const latest = pending;
      pending = null;
      scope.streamRevision += 1;
      accept(scope, latest);
      setReadError("");
      lastPaint = Date.now();
      if (!latest.busy) source?.close();
    };
    const fallback = () => {
      source?.close();
      flush();
      if (!active() || !stateRef.current?.busy) return;
      setStreamFallback(true);
      // Read the saved prefix. A transport failure never resubmits a model request.
      void load(scope);
    };
    try {
      source = new EventSource(`/api/v1/discussions/${path}/stream`);
      source.addEventListener("state", (event) => {
        if (!active()) return;
        try {
          const latest = JSON.parse((event as MessageEvent<string>).data) as DiscussionState;
          if (latest.subject.type !== type || latest.subject.id !== id) return;
          pending = latest;
          const elapsed = Date.now() - lastPaint;
          // The server sends complete prefixes; replace each message, never append it.
          if (!latest.busy || elapsed >= 80) flush();
          else if (timer === undefined) timer = window.setTimeout(flush, 80 - elapsed);
        } catch {
          fallback();
        }
      });
      source.onerror = fallback;
    } catch {
      fallback();
    }
    return () => {
      source?.close();
      if (timer !== undefined) window.clearTimeout(timer);
      pending = null;
    };
  }, [data?.busy, posting, streamFallback, path, type, id, accept, load]);

  useEffect(() => {
    if (!data?.busy || !streamFallback || readError || posting || loadingOlder) return;
    const timer = window.setTimeout(() => void load(), 1_500);
    return () => window.clearTimeout(timer);
  }, [data, streamFallback, readError, posting, loadingOlder, load]);

  async function submit(resend = false) {
    const scope = scopeRef.current;
    if (!scope?.active || scope.actionRunning || scope.olderRunning || loading || readError || stateRef.current?.busy) return;
    const pending = uncertainSends.get(key);
    if (pending && !resend) return;
    const message = pending?.message ?? (drafts.get(key) ?? "").trim();
    if (!message || message.length > 6_000) return;
    const submission = pending ?? { message, request_id: crypto.randomUUID(), output_locale: stateRef.current?.session?.response_locale ?? stateRef.current?.session?.output_locale ?? outputLocale };
    uncertainSends.set(key, submission);
    setUncertain(submission);
    scope.actionRunning = true;
    scope.revision += 1;
    for (const controller of scope.reads) controller.abort();
    const controller = new AbortController();
    scope.controllers.add(controller);
    setPosting(true);
    setStreamFallback(false);
    setActionError("");
    try {
      const latest = await request(`${path}/messages`, {
        method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(submission),
      });
      if (!current(scope, controller)) return;
      accept(scope, latest);
      uncertainSends.delete(key);
      setUncertain(null);
      if ((drafts.get(key) ?? "").trim() === submission.message) setDraft("");
      setReadError("");
    } catch (reason) {
      if (current(scope, controller)) {
        if (reason instanceof DiscussionRequestError && reason.status >= 400 && reason.status < 500 && reason.status !== 408) {
          uncertainSends.delete(key);
          setUncertain(null);
          setActionError(reason.message);
        } else {
          setActionError(uiText("{{p0}} 尚未確認是否送達；可重新讀取，或重送同一則提問。", { p0: (reason as Error).message }));
        }
      }
    } finally {
      scope.controllers.delete(controller);
      scope.actionRunning = false;
      if (current(scope, controller)) setPosting(false);
    }
  }

  async function retry(messageId: string) {
    const scope = scopeRef.current;
    if (!scope?.active || scope.actionRunning || scope.olderRunning || loading || readError || stateRef.current?.busy) return;
    scope.actionRunning = true;
    scope.revision += 1;
    for (const controller of scope.reads) controller.abort();
    const controller = new AbortController();
    scope.controllers.add(controller);
    setPosting(true);
    setStreamFallback(false);
    setActionError("");
    try {
      const latest = await request(`${path}/messages/${encodeURIComponent(messageId)}/retry`, {
        method: "POST", signal: controller.signal,
      });
      if (!current(scope, controller)) return;
      accept(scope, latest);
      setReadError("");
    } catch (reason) {
      if (current(scope, controller)) setActionError(uiText("{{p0}} 請重新讀取回覆狀態後再重試。", { p0: (reason as Error).message }));
    } finally {
      scope.controllers.delete(controller);
      scope.actionRunning = false;
      if (current(scope, controller)) setPosting(false);
    }
  }

  async function loadOlder() {
    const scope = scopeRef.current;
    const before = stateRef.current?.before_sequence;
    if (!scope?.active || scope.olderRunning || scope.actionRunning || !stateRef.current?.has_more || before == null) return false;
    scope.olderRunning = true;
    for (const previous of scope.reads) previous.abort();
    const controller = new AbortController();
    scope.controllers.add(controller);
    scope.reads.add(controller);
    const revision = scope.revision;
    setLoadingOlder(true);
    setOlderError("");
    try {
      const latest = await request(`${path}?limit=50&before_sequence=${before}`, { signal: controller.signal });
      if (!current(scope, controller) || revision !== scope.revision) return false;
      accept(scope, latest, true);
      return true;
    } catch (reason) {
      if (current(scope, controller)) setOlderError((reason as Error).message);
      return false;
    } finally {
      scope.controllers.delete(controller);
      scope.reads.delete(controller);
      scope.olderRunning = false;
      if (current(scope, controller)) setLoadingOlder(false);
    }
  }

  return {
    data, draft, setDraft, loading, posting, loadingOlder, readError, actionError,
    olderError, uncertain, streamFallback, reload: () => {
      if (!scopeRef.current?.active || scopeRef.current.actionRunning || scopeRef.current.olderRunning) return;
      setLoading(true);
      void load();
    }, submit, retry, loadOlder,
  };
}
