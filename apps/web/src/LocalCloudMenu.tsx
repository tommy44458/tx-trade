import { useEffect, useState } from "react";
import { uiText } from "./i18n/index.ts";
import { remoteView, type RemoteStatus } from "./remoteAccess.ts";
import { apiFetch } from "./transport.ts";

/** Account menu items on the computer: remote access at a glance, and cloud sign-out. */
export default function LocalCloudMenu({ onOpenSettings, onChanged }: {
  onOpenSettings: () => void;
  onChanged: () => void;
}) {
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [remote, setRemote] = useState<RemoteStatus | null>(null);
  const [busy, setBusy] = useState(false);

  // Read when the menu opens, and follow a switch while it stays open.
  useEffect(() => {
    let active = true;
    const read = () => {
      apiFetch("/api/v1/cloud-account").then((response) => response.ok ? response.json() : null)
        .then((value) => { if (active && value) setSignedIn(!!value.signed_in); }).catch(() => {});
      apiFetch("/api/v1/cloud-account/remote").then((response) => response.ok ? response.json() : null)
        .then((value) => { if (active && value) setRemote(value); }).catch(() => {});
    };
    read();
    const timer = window.setInterval(read, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  const toggle = async () => {
    if (!remote) return;
    const enabled = !remote.enabled;
    setRemote({ ...remote, enabled, state: enabled ? "connecting" : "disabled" });
    setBusy(true);
    try {
      const response = await apiFetch(`/api/v1/cloud-account/remote/${enabled ? "enable" : "disable"}`, { method: "POST" });
      if (response.ok) setRemote(await response.json());
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    try {
      await apiFetch("/api/v1/cloud-account/sign-out", { method: "POST" });
      setSignedIn(false);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  if (signedIn === null) return null;
  if (!signedIn)
    return (
      <button type="button" className="account-menu-item" data-menu-close onClick={onOpenSettings}>
        {uiText("登入雲端帳戶以遠端使用")}
      </button>
    );
  const view = remote ? remoteView(remote) : null;
  return (
    <>
      <div className="account-menu-row">
        <span className="account-menu-row-copy">
          <span id="account-menu-remote">{uiText("遠端存取")}</span>
          {view && <small className="cloud-remote-status" data-tone={view.tone}>
            <span className="cloud-remote-dot" aria-hidden="true" />{view.text}
          </small>}
        </span>
        <button type="button" role="switch" className="cloud-switch" aria-checked={!!remote?.enabled}
          aria-labelledby="account-menu-remote" disabled={!remote || busy} onClick={() => void toggle()}>
          <span className="cloud-switch-thumb" aria-hidden="true" />
        </button>
      </div>
      <hr className="account-menu-separator" />
      <button type="button" className="account-menu-item destructive" data-menu-close disabled={busy} onClick={() => void signOut()}>
        {uiText("登出雲端帳戶")}
      </button>
    </>
  );
}
