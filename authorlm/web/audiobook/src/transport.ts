// POST /api/audiobook → audiobook.dispatch, POST /api/workbench →
// workbench.dispatch (authorlm/audiobook_server.py). Every request names the
// manuscript chosen in the page's dropdown (setManuscript); before one is
// chosen the server falls back to its -m flag.
export interface Transport {
  request<T>(method: string, params: Record<string, unknown>): Promise<T>;
}

let manuscript: string | null = null;
export function setManuscript(name: string | null): void { manuscript = name; }

function road(path: string): Transport {
  return {
    async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      const named = manuscript && params.manuscript == null ? {...params, manuscript} : params;
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({method, params: named}),
      });
      const body = await response.json();
      if (!response.ok || body.ok === false) throw new Error(body.error || `Request failed (${response.status})`);
      return body.result as T;
    },
  };
}

export const audiobookTransport = road("/api/audiobook");
export const workbenchTransport = road("/api/workbench");
export const request = audiobookTransport.request;
