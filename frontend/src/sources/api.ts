import type { SourcePayload } from './model';

async function response<T>(request: Promise<Response>): Promise<T> {
  const res = await request;
  const body = await res.json() as T & { message?: string };
  if (!res.ok || (body as {status?: string}).status === 'error') {
    throw new Error(body.message || `HTTP ${res.status}`);
  }
  return body;
}
export const getSourcesStatus = () => response<SourcePayload>(fetch('/api/sources/status', {
  headers: { Accept: 'application/json' }, cache: 'no-store',
}));
export function previewSourcesConfig(payload: FormData) {
  return response<SourcePayload>(fetch('/api/sources/gui/preview', { method: 'POST', body: payload }));
}
export function saveSourcesConfig(payload: FormData) {
  return response<SourcePayload>(fetch('/api/sources/config', { method: 'POST', body: payload }));
}
export function runSourceAction(payload: FormData) {
  return response<SourcePayload>(fetch('/api/sources/action', { method: 'POST', body: payload }));
}
