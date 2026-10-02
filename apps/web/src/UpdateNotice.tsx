import { useEffect, useState } from "react";
import { AnalysisSpinner } from "./AnalysisProgress";
import type { DesktopUpdateState } from "./desktop";
import { uiText } from "./i18n/index.ts";
import "./UpdateNotice.css";

/**
 * Stays above the account while a newer version is available, downloading, or ready, so an
 * update put off with "Later" is not forgotten. Selecting it opens the native update dialog.
 */
export default function UpdateNotice() {
  const bridge = typeof window === "undefined" ? undefined : window.tradeHelper;
  const [state, setState] = useState<DesktopUpdateState>(null);
  useEffect(() => {
    if (!bridge?.onUpdateState || !bridge.updateState) return;
    let active = true;
    const stop = bridge.onUpdateState((next) => { if (active) setState(next); });
    bridge.updateState().then((current) => { if (active) setState(current); }).catch(() => {});
    return () => { active = false; stop(); };
  }, [bridge]);
  if (!state?.version) return null;
  const label = state.canInstall && state.status !== "installing"
    ? uiText("重新啟動以更新到 {{p0}}", { p0: state.version })
    : state.status === "available" ? uiText("有新版本 {{p0}}", { p0: state.version })
      : state.status === "downloading" ? uiText("正在下載更新 {{p0}}%", { p0: state.percent })
        : state.status === "waiting-for-idle" ? uiText("分析結束後安裝更新")
          : state.status === "installing" ? uiText("正在安裝更新…") : null;
  if (!label) return null;
  // Busy from the moment Download is chosen, so the app never looks stuck while it works.
  const busy = !state.canInstall && ["downloading", "waiting-for-idle", "installing"].includes(state.status);
  const downloading = state.status === "downloading";
  return (
    <button type="button" className="update-notice" data-ready={state.canInstall || undefined} aria-busy={busy || undefined}
      onClick={() => void bridge?.showUpdate?.()}>
      {busy ? <AnalysisSpinner /> : <span className="update-notice-dot" aria-hidden="true" />}
      <span className="update-notice-label" role="status">{label}</span>
      {downloading && (
        <span className="update-notice-progress" aria-hidden="true">
          <span style={{ width: `${Math.max(2, state.percent)}%` }} />
        </span>
      )}
    </button>
  );
}
