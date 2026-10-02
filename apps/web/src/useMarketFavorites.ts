import { uiText } from "./i18n/index.ts";
import { useEffect, useRef, useState } from "react";
import { settingsRequest, type LocalSettings } from "./localSettings";

/** Keep rapid toggles in order so a slower earlier request cannot overwrite them. */
export default function useMarketFavorites() {
  const [favoriteIds, setFavoriteIds] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const desired = useRef<string[]>([]);
  const persisted = useRef<string[]>([]);
  const queue = useRef(Promise.resolve());
  const pending = useRef(0);

  useEffect(() => {
    let active = true;
    settingsRequest<LocalSettings>("/settings")
      .then((settings) => {
        if (!active) return;
        const ids = [...new Set(settings.favorite_market_ids ?? [])];
        desired.current = ids;
        persisted.current = ids;
        setFavoriteIds(ids);
        setReady(true);
      })
      .catch(() => {
        if (active) setError(uiText("無法載入最愛交易對，請稍後重新開啟應用程式。"));
      });
    return () => {
      active = false;
    };
  }, []);

  function toggleFavorite(id: string) {
    if (!ready) return;
    const next = desired.current.includes(id)
      ? desired.current.filter((value) => value !== id)
      : [...desired.current, id];
    desired.current = next;
    setFavoriteIds(next);
    setError("");
    pending.current += 1;
    setSaving(true);
    queue.current = queue.current
      .then(async () => {
        const settings = await settingsRequest<LocalSettings>("/settings", {
          method: "PATCH",
          body: JSON.stringify({ favorite_market_ids: next }),
        });
        persisted.current = settings.favorite_market_ids ?? next;
        if (desired.current === next) {
          desired.current = persisted.current;
          setFavoriteIds(persisted.current);
          setError("");
        }
      })
      .catch((failure: unknown) => {
        if (desired.current !== next) return;
        desired.current = persisted.current;
        setFavoriteIds(persisted.current);
        setError(
          failure instanceof Error
            ? uiText("最愛交易對未儲存：{{p0}}", { p0: failure.message })
            : uiText("最愛交易對未儲存，請再試一次。"),
        );
      })
      .finally(() => {
        pending.current -= 1;
        setSaving(pending.current > 0);
      });
  }

  return { favoriteIds, ready, saving, error, toggleFavorite };
}
