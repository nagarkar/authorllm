// POST /api/audiobook → audiobook.dispatch, POST /api/workbench →
// workbench.dispatch (authorlm/audiobook_server.py). The manuscript comes
// from the server's -m flag; the page never names it.
export interface Transport {
  request<T>(method: string, params: Record<string, unknown>): Promise<T>;
}

function road(path: string): Transport {
  return {
    async request<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({method, params}),
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
