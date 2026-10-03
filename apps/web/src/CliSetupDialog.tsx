import { useEffect, useRef, useState } from "react";
import { uiText } from "./i18n/index.ts";
import Icon from "./Icon";
import { AnalysisSpinner } from "./AnalysisProgress";
import "./CliSetupDialog.css";

export type CliSetupProvider = "codex" | "claude_code";
export type CliSetupReason = "install" | "signin";

// Shell commands are product-neutral and are not translated.
const INSTALL_COMMANDS: Record<CliSetupProvider, string[]> = {
  codex: ["npm install -g @openai/codex", "brew install --cask codex"],
  claude_code: ["curl -fsSL https://claude.ai/install.sh | bash", "npm install -g @anthropic-ai/claude-code"],
};
// Windows runs these in PowerShell; Homebrew and bash installers do not exist there.
const WINDOWS_INSTALL_COMMANDS: Record<CliSetupProvider, string[]> = {
  codex: ["npm install -g @openai/codex"],
  claude_code: ["irm https://claude.ai/install.ps1 | iex", "npm install -g @anthropic-ai/claude-code"],
};
const SIGN_IN_COMMAND = "claude auth login";

function Command({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="cli-setup-command">
      <code>{value}</code>
      <button type="button" className="settings-secondary" onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => setCopied(true), () => {});
      }}>{copied ? uiText("已複製") : uiText("複製")}</button>
    </div>
  );
}

export default function CliSetupDialog({ provider, reason, checking, error, onRecheck, onClose }: {
  provider: CliSetupProvider;
  reason: CliSetupReason;
  checking: boolean;
  error: string;
  onRecheck: () => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const recheck = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
    recheck.current?.focus();
    return () => element?.close();
  }, []);
  const claude = provider === "claude_code";
  const windows = window.tradeHelper?.platform === "win32";
  const commands = (windows ? WINDOWS_INSTALL_COMMANDS : INSTALL_COMMANDS)[provider];
  const title = reason === "signin" ? uiText("Claude Code 尚未登入")
    : claude ? uiText("找不到 Claude Code CLI") : uiText("找不到 Codex CLI");
  return (
    <dialog ref={dialog} className="cli-setup-dialog" aria-labelledby="cli-setup-title"
      onCancel={event => { event.preventDefault(); onClose(); }}>
      <div className="cli-setup-head">
        <h2 id="cli-setup-title">{title}</h2>
        <button type="button" className="cli-setup-close" onClick={onClose} aria-label={uiText("關閉")}>
          <Icon name="close" />
        </button>
      </div>
      <p className="settings-help">
        {reason === "signin"
          ? uiText("Claude Code 已安裝，但尚未登入 Claude 帳號。請依下列步驟完成後重新檢查。")
          : claude
            ? uiText("使用 Claude Code 分析前，需要先在這台電腦安裝 Claude Code CLI 並登入 Claude 帳號。")
            : uiText("使用 Codex 分析前，需要先在這台電腦安裝 Codex CLI。")}
      </p>
      <ol className="cli-setup-steps">
        {reason === "install" && (
          <li>
            <p>{windows ? uiText("開啟「PowerShell」，執行以下其中一個安裝指令：")
              : uiText("開啟「終端機」，執行以下其中一個安裝指令：")}</p>
            {commands.map(command => <Command key={command} value={command} />)}
          </li>
        )}
        {claude ? (
          <li>
            <p>{windows ? uiText("在 PowerShell 登入 Claude 帳號，並依瀏覽器指示完成授權：")
              : uiText("在終端機登入 Claude 帳號，並依瀏覽器指示完成授權：")}</p>
            <Command value={SIGN_IN_COMMAND} />
          </li>
        ) : (
          <li><p>{uiText("安裝完成後按「重新檢查」，再按「連線 Codex」以 ChatGPT 帳號登入。")}</p></li>
        )}
        {claude && <li><p>{uiText("完成後回到這裡按「重新檢查」。")}</p></li>}
      </ol>
      {error && <p className="settings-inline-error" role="alert">{error}</p>}
      <div className="settings-actions">
        <button type="button" className="action" onClick={onRecheck} disabled={checking} aria-busy={checking}
          ref={recheck}>
          {checking && <AnalysisSpinner />}{" "}{uiText("重新檢查")}
        </button>
        <button type="button" className="settings-secondary" onClick={onClose}>{uiText("稍後再說")}</button>
      </div>
    </dialog>
  );
}
