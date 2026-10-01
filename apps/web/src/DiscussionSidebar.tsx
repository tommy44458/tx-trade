import { uiText } from "./i18n/index.ts";
import { useEffect, useId, useRef, useState } from "react";
import AnalysisDiscussion, { type AnalysisDiscussionProps } from "./AnalysisDiscussion";
import Icon from "./Icon";
import "./DiscussionSidebar.css";

export default function DiscussionSidebar({
  subject,
  available,
}: {
  subject: AnalysisDiscussionProps | null;
  available: boolean;
}) {
  const [overlay, setOverlay] = useState(() => window.matchMedia("(max-width: 1240px)").matches);
  const [open, setOpen] = useState(() => !window.matchMedia("(max-width: 1240px)").matches);
  const panelId = useId();
  const titleId = useId();
  const emptyInputId = useId();
  const panel = useRef<HTMLElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const query = window.matchMedia("(max-width: 1240px)");
    const changed = (event: MediaQueryListEvent) => setOverlay(event.matches);
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);

  useEffect(() => {
    if (!open || !available || !overlay) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeButton.current?.focus({ preventScroll: true });
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        window.requestAnimationFrame(() => launcher.current?.focus({ preventScroll: true }));
        return;
      }
      if (event.key !== "Tab") return;
      const elements = [...(panel.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), a[href], [tabindex="0"]',
      ) ?? [])].filter((element) => element.getClientRects().length > 0);
      const first = elements[0];
      const last = elements.at(-1);
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus({ preventScroll: true });
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus({ preventScroll: true });
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKey);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [open, available, overlay]);

  function close() {
    setOpen(false);
    window.requestAnimationFrame(() => launcher.current?.focus({ preventScroll: true }));
  }

  return <>
    {available && !open && <button
      ref={launcher}
      type="button"
      className="discussion-sidebar-toggle"
      aria-label={uiText("展開 AI 追問")}
      aria-expanded={false}
      aria-controls={panelId}
      onClick={() => setOpen(true)}
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 11.5a8 8 0 0 1-8 8H4l1.5-4A8 8 0 1 1 20 11.5Z" /><path d="M8 10h8M8 14h5" /></svg>
      <span>{uiText("AI 追問")}</span>
    </button>}{" "}
    {available && open && overlay && <div className="discussion-backdrop" aria-hidden="true" onClick={close} />}
    <aside
      ref={panel}
      id={panelId}
      className="discussion-sidebar"
      data-open={available && open}
      data-mode={overlay ? "drawer" : "side"}
      hidden={!available || !open}
      role={overlay ? "dialog" : "complementary"}
      aria-modal={overlay && available && open ? true : undefined}
      aria-labelledby={titleId}
    >
      <header className="discussion-sidebar-heading">
        <div><h2 id={titleId}>{uiText("AI 追問")}</h2><p>{subject ? subject.contextLabel : uiText("尚無分析結果")}</p></div>
        <button ref={closeButton} type="button" className="discussion-sidebar-close" aria-label={uiText("收合 AI 追問")} onClick={close}><Icon name="close" /></button>
      </header>
      <div className="discussion-sidebar-body">
        {subject && available ? <AnalysisDiscussion {...subject} /> : <section className="discussion-unavailable" aria-label={uiText("尚未提供追問")}>
          <div className="discussion-unavailable-copy"><strong>{uiText("完成分析後即可追問")}</strong><p>{uiText("先取得市場、持倉或宏觀分析結果，對話會帶入該次分析的資料。")}</p></div>
          <form className="discussion-composer discussion-composer-disabled" onSubmit={(event) => event.preventDefault()}>
            <label className="discussion-visually-hidden" htmlFor={emptyInputId}>{uiText("追問內容")}</label>
            <textarea id={emptyInputId} disabled rows={3} placeholder={uiText("先完成分析，再討論這份結果")} />
            <div className="discussion-composer-footer"><p>{uiText("尚無可討論的分析結果")}</p><button type="submit" className="discussion-send" disabled aria-label={uiText("送出追問")}><Icon name="arrow" /></button></div>
          </form>
        </section>}
      </div>
    </aside>
  </>;
}
