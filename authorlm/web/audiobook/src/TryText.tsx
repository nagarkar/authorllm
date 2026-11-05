import { useEffect, useRef, useState, type FormEvent } from "react";

export function TryText() {
  const [markdown, setMarkdown] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const player = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    if (!audioUrl) return;
    // If the browser blocks autoplay after rendering, its play control remains.
    void player.current?.play().catch(() => {});
    return () => URL.revokeObjectURL(audioUrl);
  }, [audioUrl]);

  async function listen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!markdown.trim() || busy) return;
    setBusy(true);
    setError("");
    setAudioUrl("");
    try {
      const response = await fetch("/api/try-text", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-AuthorLM": "1" },
        body: JSON.stringify({ markdown }),
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        throw new Error(failure.error || "Could not read the pasted text.");
      }
      setAudioUrl(URL.createObjectURL(await response.blob()));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page">
      <header>
        <h1 className="display"><img className="brand-icon" src="/icon-192.png" alt="AuthorLM" />Try text</h1>
        <nav className="modes" aria-label="Audiobook navigation">
          <a className="chip" href={`/${location.hash}`}>Back to audiobook</a>
        </nav>
      </header>
      <form className="card try-text" onSubmit={listen}>
        <label htmlFor="try-markdown">Paste Markdown or plain text</label>
        <p id="try-hint">Listen with the Mac preview voice. Your paste stays separate from the manuscript.</p>
        <textarea id="try-markdown" aria-describedby="try-hint" value={markdown}
          readOnly={busy} onChange={event => {
            setMarkdown(event.target.value);
            setAudioUrl("");
            setError("");
          }} />
        <button className="btn preview on" type="submit" disabled={busy || !markdown.trim()}>
          {busy ? "Preparing audio…" : "▶ Listen"}
        </button>
        {error && <p className="bad" role="alert">{error}</p>}
        {audioUrl && <audio ref={player} src={audioUrl} controls aria-label="Pasted text audio" />}
      </form>
    </main>
  );
}
