import { useState } from "react";
import { useApp } from "@modelcontextprotocol/ext-apps/react";
import type { App } from "@modelcontextprotocol/ext-apps";
import type { Transport } from "./types";
import { TriageApp } from "./TriageApp";

function unwrap(value: unknown): unknown {
  let body = value as {ok?: boolean; result?: unknown; error?: string};
  if (body && typeof body === "object" && "structuredContent" in body) {
    const result = body as {structuredContent?: unknown; content?: Array<{type: string; text?: string}>};
    if (result.structuredContent) body = result.structuredContent as typeof body;
    else {
      const text = result.content?.find((item) => item.type === "text")?.text;
      if (text) body = JSON.parse(text) as typeof body;
    }
  }
  if (body && body.ok === false) throw new Error(body.error || "AuthorLM request failed");
  return body && "result" in body ? body.result : body;
}

const httpTransport: Transport = {
  async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
    const response = await fetch("/api/triage", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({method, params}),
    });
    const body = await response.json();
    if (!response.ok || body.ok === false) throw new Error(body.error || `Request failed (${response.status})`);
    return unwrap(body) as T;
  },
};

function mcpTransport(app: App): Transport {
  return {
    async request<T>(method: string, params: Record<string, unknown>): Promise<T> {
      const result = await app.callServerTool({
        name: "triage_app_request",
        arguments: {method, params},
      });
      return unwrap(result) as T;
    },
  };
}

function McpRoot() {
  const [manuscript, setManuscript] = useState<string>();
  const {app, isConnected, error} = useApp({
    appInfo: {name: "AuthorLM Triage App", version: "0.1.0"},
    capabilities: {},
    autoResize: true,
    onAppCreated(instance) {
      instance.addEventListener("toolinput", (input) => {
        const args = input.arguments as {manuscript?: string} | undefined;
        if (args?.manuscript) setManuscript(args.manuscript);
      });
      instance.addEventListener("toolresult", (result) => {
        try {
          const data = unwrap(result) as {manuscript?: string};
          if (data?.manuscript) setManuscript(data.manuscript);
        } catch {
          // The main app will surface request errors.
        }
      });
    },
  });
  if (error) return <div className="boot-error">Could not connect to the MCP host: {error.message}</div>;
  if (!isConnected || !app) return <div className="boot-screen"><span />Opening the editorial desk...</div>;
  return <ConnectedMcpApp app={app} manuscript={manuscript} />;
}

function ConnectedMcpApp({app, manuscript}: {app: App; manuscript?: string}) {
  const [transport] = useState(() => mcpTransport(app));
  return <TriageApp transport={transport} manuscript={manuscript} />;
}

export function AppRoot() {
  if (window.parent !== window) return <McpRoot />;
  return <TriageApp transport={httpTransport} />;
}
