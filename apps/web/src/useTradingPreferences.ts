import { uiText } from "./i18n/index.ts";
import { useCallback, useEffect, useRef, useState } from "react";
import { settingsRequest, type LocalSettings, type TradingPreferences } from "./localSettings";

/** Persist explicit changes only. Viewing a historical report does not change defaults. */
export default function useTradingPreferences(
  onLoaded: (settings: LocalSettings, touched: Set<keyof TradingPreferences>) => void,
) {
  const [initialSettings, setInitialSettings] = useState<LocalSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const touched = useRef(new Set<keyof TradingPreferences>());
  const pending = useRef<Partial<TradingPreferences>>({});
  const initialized = useRef(false);
  const writing = useRef(false);
  const mounted = useRef(false);
  const loadedCallback = useRef(onLoaded);
  useEffect(() => { loadedCallback.current = onLoaded; }, [onLoaded]);

  const flush = useCallback(async function sendPending() {
    if (!initialized.current || writing.current || !Object.keys(pending.current).length) return;
    const patch = pending.current;
    pending.current = {};
    writing.current = true;
    if (mounted.current) { setSaving(true); setError(""); }
    let failed = false;
    try {
      await settingsRequest<LocalSettings>("/settings", {
        method: "PATCH",
        body: JSON.stringify({ trading_preferences: patch }),
      });
    } catch (reason) {
      // Keep newer changes if they arrived while an earlier save was in flight.
      pending.current = { ...patch, ...pending.current };
      failed = true;
      if (mounted.current) setError(uiText("交易偏好未儲存：{{p0}}", { p0: (reason as Error).message }));
    } finally {
      writing.current = false;
      if (!failed && Object.keys(pending.current).length) void sendPending();
      else if (mounted.current) setSaving(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let active = true;
    settingsRequest<LocalSettings>("/settings")
      .then((settings) => {
        if (!active) return;
        setInitialSettings(settings);
        setError("");
        loadedCallback.current(settings, touched.current);
        initialized.current = true;
        void flush();
      })
      .catch((reason: Error) => { if (active) setError(uiText("無法讀取交易偏好：{{p0}}", { p0: reason.message })); });
    return () => { active = false; mounted.current = false; };
  }, [flush, loadAttempt]);

  function save(patch: Partial<TradingPreferences>) {
    for (const key of Object.keys(patch) as (keyof TradingPreferences)[]) touched.current.add(key);
    pending.current = { ...pending.current, ...patch };
    void flush();
  }

  function retry() {
    if (!initialSettings) setLoadAttempt((value) => value + 1);
    else void flush();
  }

  return { initialSettings, touched, save, saving, error, retry };
}
