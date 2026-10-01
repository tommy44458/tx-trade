import { uiText, type UiLocale } from "./i18n/index.ts";
import { useEffect, useRef, useState } from "react";
import type { MacroInterpretationStatus } from "./MacroInterpretationPanel";

async function request(path: string, options: RequestInit): Promise<MacroInterpretationStatus> {
  const response = await fetch(`/api/v1/events/macro-interpretation${path}`, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string"
    ? body.detail : body.detail?.message ?? uiText("宏觀解讀讀取失敗 ({{p0}})", { p0: response.status }));
  return body;
}

export default function useMacroInterpretation(active: boolean, locale: UiLocale) {
  const [data, setData] = useState<MacroInterpretationStatus | null>(null);
  const [error, setError] = useState("");
  const [ensuring, setEnsuring] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const retryRequested = useRef(false);
  const localeRef = useRef(locale);
  useEffect(() => { localeRef.current = locale; }, [locale]);
  const interpretationRef = useRef(data?.interpretation?.id);
  useEffect(() => { interpretationRef.current = data?.interpretation?.id; }, [data?.interpretation?.id]);
  const translationPending = useRef(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();
    let retry = retryRequested.current;
    retryRequested.current = false;

    async function load() {
      let nextDelay = 30_000;
      try {
        let latest = await request(`?output_locale=${locale}`, { signal: controller.signal });
        if (cancelled) return;
        setData(latest);
        setError("");
        if (latest.status === "missing" && latest.needs_update ||
          retry && ["failed", "unconfigured"].includes(latest.status)) {
          setEnsuring(true);
          const explicitRetry = retry;
          retry = false;
          latest = await request("/ensure", {
            method: "POST", signal: controller.signal,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ retry: explicitRetry, output_locale: locale }),
          });
          if (cancelled) return;
          setData(latest);
        }
        retry = false;
        if (latest.status === "running" || ["queued", "running"].includes(latest.translation?.status ?? "")) nextDelay = 1_500;
      } catch (reason) {
        if (!cancelled) setError((reason as Error).message);
      } finally {
        if (!cancelled) {
          setEnsuring(false);
          timer = window.setTimeout(load, nextDelay);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [active, locale, refreshVersion]);

  async function translate() {
    const id = data?.interpretation?.id;
    if (!id || translationPending.current) return;
    const target = locale;
    translationPending.current = true;
    setTranslating(true);
    setError("");
    try {
      const latest = await request(`/${encodeURIComponent(id)}/translate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ output_locale: target, retry: data?.translation?.status === "failed" }),
      });
      // A request keeps its original language even if the interface changes meanwhile.
      if (localeRef.current === target && interpretationRef.current === id) setData(latest);
      setRefreshVersion((version) => version + 1);
    } catch (reason) {
      if (localeRef.current === target) setError((reason as Error).message);
    } finally {
      translationPending.current = false;
      setTranslating(false);
    }
  }

  return {
    data, error, ensuring, translating, translate,
    refresh: () => setRefreshVersion((version) => version + 1),
    retry: () => {
      retryRequested.current = true;
      setRefreshVersion((version) => version + 1);
    },
  };
}
