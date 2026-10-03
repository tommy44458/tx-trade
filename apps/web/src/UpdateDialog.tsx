import { useEffect, useRef, useState } from "react";
import type { DesktopUpdatePrompt } from "./desktop";
import "./UpdateDialog.css";

/** Release notes as plain text: "### " lines are section labels, "- " lines are items. No markup is rendered. */
function Notes({ text }: { text: string }) {
  const blocks: { kind: "label" | "item" | "text"; value: string }[] = text.split("\n")
    .map((line) => line.trim()).filter(Boolean)
    .map((line) => line.startsWith("#") ? { kind: "label", value: line.replace(/^#+\s*/, "") }
      : line.startsWith("- ") ? { kind: "item", value: line.slice(2) } : { kind: "text", value: line });
  const out: React.ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length) out.push(<ul key={`list-${out.length}`}>{items.map((item, i) => <li key={i}>{item}</li>)}</ul>);
    items = [];
  };
  for (const block of blocks) {
    if (block.kind === "item") { items.push(block.value); continue; }
    flush();
    out.push(block.kind === "label" ? <h3 key={out.length}>{block.value}</h3> : <p key={out.length}>{block.value}</p>);
  }
  flush();
  return <>{out}</>;
}

/**
 * The update window inside the app. Its notes scroll, so a long release never covers the screen; the
 * desktop shell falls back to a native dialog until this has registered.
 */
export default function UpdateDialog() {
  const bridge = typeof window === "undefined" ? undefined : window.tradeHelper;
  const [prompt, setPrompt] = useState<DesktopUpdatePrompt | null>(null);
  const primary = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!bridge?.onUpdatePrompt || !bridge.updatePromptReady) return;
    const stop = bridge.onUpdatePrompt(setPrompt);
    void bridge.updatePromptReady().catch(() => {});
    return stop;
  }, [bridge]);
  useEffect(() => {
    if (!prompt) return;
    primary.current?.focus();
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setPrompt(null); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [prompt]);
  if (!prompt) return null;
  const respond = () => {
    const action = prompt.action;
    setPrompt(null);
    if (action) void bridge?.updateRespond?.(action).catch(() => {});
  };
  return (
    <div className="update-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setPrompt(null); }}>
      <section className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title" data-tone={prompt.tone}>
        <header>
          <h2 id="update-dialog-title">{prompt.message}</h2>
          {prompt.details.map((line, i) => <p key={i}>{line}</p>)}
        </header>
        {prompt.notes && <div className="update-dialog-notes" tabIndex={0}><Notes text={prompt.notes} /></div>}
        <footer>
          <button type="button" className="secondary-button" ref={prompt.action ? undefined : primary} onClick={() => setPrompt(null)}>
            {prompt.dismissLabel}
          </button>
          {prompt.action && prompt.actionLabel && (
            <button type="button" className="action" ref={primary} onClick={respond}>{prompt.actionLabel}</button>
          )}
        </footer>
      </section>
    </div>
  );
}
