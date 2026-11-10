// POST /api/audiobook → audiobook.dispatch, POST /api/workbench →
// workbench.dispatch, POST /api/devedit → devedit.dispatch
// (authorlm/audiobook_server.py). Every request names the manuscript chosen
// in the page's dropdown (setManuscript); before one is chosen the server
// falls back to its -m flag.
export interface Transport {
  request<T>(method: string, params: Record<string, unknown>): Promise<T>;
}

let manuscript: string | null = null;
export function setManuscript(name: string | null): void { manuscript = name; }

function road(path: string): Transport {
  return {
    async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      const named = manuscript && params.manuscript == null ? {...params, manuscript} : params;
      const init = {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({method, params: named}),
      };
      // A request cut off mid-flight (the phone suspended the page, or the
      // server restarted) surfaces as a TypeError, "Load failed" in Safari.
      // Try once more after a short pause before showing it.
      let response: Response;
      try {
        response = await fetch(path, init);
      } catch {
        await new Promise((r) => setTimeout(r, 800));
        response = await fetch(path, init);
      }
      const body = await response.json();
      if (!response.ok || body.ok === false) throw new Error(body.error || `Request failed (${response.status})`);
      return body.result as T;
    },
  };
}

export const audiobookTransport = road("/api/audiobook");
export const workbenchTransport = road("/api/workbench");
export const deveditTransport = road("/api/devedit");
export const request = audiobookTransport.request;
