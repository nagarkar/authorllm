import type { Transport } from "./types";
import { TriageApp } from "./TriageApp";

// The one road: `authorlm triage-app` serves this page and answers
// POST /api/triage with triage_transport.dispatch.
const httpTransport: Transport = {
  async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    const response = await fetch("/api/triage", {
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
  return <TriageApp transport={httpTransport} />;
}
