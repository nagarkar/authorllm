import { Workbench } from "./Workbench";

export interface Transport {
  request<T>(method: string, params: Record<string, unknown>): Promise<T>;
}

// The one road: `authorlm workbench` serves this page and answers
// POST /api/workbench with workbench.dispatch. The manuscript comes from
// the server's -m flag, so the page never has to name it.
const httpTransport: Transport = {
  async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    const response = await fetch("/api/workbench", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({method, params}),
    });
    const body = await response.json();
    if (!response.ok || body.ok === false) throw new Error(body.error || `Request failed (${response.status})`);
    return body.result as T;
  },
};

export function AppRoot() {
  return <Workbench transport={httpTransport} />;
}
