import { useEffect, useRef, useState, type ReactNode } from "react";
import { uiText } from "./i18n/index.ts";
import Icon from "./Icon";
import type { SessionAccount } from "./sessionIdentity";
import "./AccountMenu.css";

/**
 * The sidebar account, opening a menu with settings and what the account controls:
 * remote access on the computer, or the computers on the remote page. Any menu
 * button marked `data-menu-close` closes the menu after it runs.
 */
export default function AccountMenu({ account, settingsActive, onOpenSettings, children }: {
  account: SessionAccount | null;
  settingsActive: boolean;
  onOpenSettings: () => void;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !root.current?.contains(event.target as Node))
        setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const name = account?.name ?? uiText("本地工作空間");
  return (
    <div className="sidebar-foot account-menu" ref={root} onClick={(event) => {
      if ((event.target as Element).closest("[data-menu-close]")) setOpen(false);
    }}>
      {open && (
        <div className="account-menu-panel" id="account-menu-panel">
          {account && (
            <div className="account-menu-identity">
              <span className="avatar" aria-hidden="true">{account.initial}</span>
              <span>
                <strong>{account.name}</strong>
                {account.detail && <small>{account.detail}</small>}
              </span>
            </div>
          )}
          {children}
          <hr className="account-menu-separator" />
          <button type="button" className="account-menu-item" data-menu-close aria-current={settingsActive ? "page" : undefined}
            onClick={onOpenSettings}>
            <Icon name="settings" />{uiText("設定")}
          </button>
        </div>
      )}
      <button type="button" className={`account-menu-trigger${settingsActive ? " active" : ""}`} aria-expanded={open}
        aria-controls="account-menu-panel" aria-label={uiText("帳戶與設定")} onClick={() => setOpen((value) => !value)}>
        <span className="avatar" aria-hidden="true">{account?.initial ?? <Icon name="settings" />}</span>
        <span className="account-menu-copy">
          <strong>{name}</strong>
          {account?.detail && <small>{account.detail}</small>}
        </span>
        <svg className="account-menu-chevron" viewBox="0 0 10 16" aria-hidden="true">
          <path d="M2 6l3-3 3 3M2 10l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    </div>
  );
}
