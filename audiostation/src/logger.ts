// Frontend structured logger — writes to the Rust log file via Tauri commands.
// Falls back to console.* if the invoke fails (e.g. during HMR reload).

import { invoke } from '@tauri-apps/api/core';

function safeStringify(value: unknown): string {
  if (value instanceof Error) {
    return `${value.message}${value.stack ? '\n' + value.stack : ''}`;
  }
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function logError(context: string, error: unknown): void {
  const message = safeStringify(error);
  console.error(`[${context}]`, message);
  invoke('log_frontend_error', { context, message }).catch(() => {});
}

export function logInfo(context: string, message: string): void {
  console.info(`[${context}]`, message);
  invoke('log_frontend_info', { context, message }).catch(() => {});
}
