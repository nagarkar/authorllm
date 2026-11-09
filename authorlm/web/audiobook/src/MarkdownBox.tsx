import { useEffect, useMemo, useRef, useState } from "react";
import { markdownToHtml } from "./markdown";

// Shows Markdown as formatted text; a click turns it into the raw Markdown
// for editing, and Esc or a click outside turns it back and saves it.
export function MarkdownBox({ value, onSave, label }: { value: string; onSave: (text: string) => Promise<boolean>; label: string }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [note, setNote] = useState("");
  const area = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => { if (!editing) setDraft(value); }, [value, editing]);
  useEffect(() => {
    if (editing && area.current) {
      area.current.focus();
      area.current.style.height = `${area.current.scrollHeight + 4}px`;
    }
  }, [editing]);
  const html = useMemo(() => markdownToHtml(draft), [draft]);

  const finish = async () => {
    setEditing(false);
    if (draft === value) return;
    setNote("saving…");
    setNote(await onSave(draft) ? `saved ${new Date().toLocaleTimeString()}` : "not saved — see the message above");
  };

  return (
    <div className="card md-box">
      {editing ? (
        <textarea ref={area} className="md-raw" value={draft} aria-label={`${label} (Markdown)`}
          onChange={e => { setDraft(e.target.value); e.target.style.height = `${e.target.scrollHeight + 4}px`; }}
          onBlur={() => void finish()}
          onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); (e.target as HTMLTextAreaElement).blur(); } }} />
      ) : (
        <div className="md-view" role="button" tabIndex={0} title="Click to edit the Markdown"
          onClick={() => setEditing(true)} onKeyDown={e => { if (e.key === "Enter") setEditing(true); }}
          dangerouslySetInnerHTML={{ __html: html || "<p class='muted'>Empty. Click to write.</p>" }} />
      )}
      {note && <div className="muted md-note">{note}</div>}
    </div>
  );
}
