import type { MinersGuiPayload, MinersLivePayload } from './model';

export async function unpack<T>(promise: Promise<Response>): Promise<T> {
  const r = await promise;
  if (!r.headers.get('content-type')?.includes('application/json')) {
    throw new Error(`Miner-API: HTTP ${r.status} – keine gültige JSON-Antwort`);
  }
  let data: T & { status?: string; message?: string };
  try {
    data = await r.json() as T & { status?: string; message?: string };
  } catch {
    throw new Error(`Miner-API: HTTP ${r.status} – fehlerhafte JSON-Antwort`);
  }
  if (!r.ok || data.status === 'error') {
    throw new Error(data.message || `Miner-API: HTTP ${r.status}`);
  }
  return data;
}
const get = (url: string) => fetch(url, { headers: {Accept:'application/json'}, cache: 'no-store' });
const postForm = (url: string, form: FormData) => unpack<{status:string;message?:string;miner_id?:string;control_enabled?:boolean;monitor_enabled?:boolean}>(fetch(url, { method:'POST', body:form }));
export const loadMinerGui = () => unpack<MinersGuiPayload>(get('/api/miners/gui'));
export const loadMinerLive = (expandedIds: string[]) => {
  const query = new URLSearchParams();
  if (expandedIds.length) query.set('open_ids',expandedIds.join(','));
  return unpack<MinersLivePayload>(get('/api/miners/status'+(query.size ? '?'+query.toString() : '')));
};
export const addMiner = (form: FormData) => postForm('/api/miners/add', form);
export const saveMiner = (id: string, form: FormData) => postForm(`/api/miner/${encodeURIComponent(id)}/config`, form);
export const applyDeviceSettings = (id: string, form: FormData) => postForm(`/api/miner/${encodeURIComponent(id)}/device-settings`, form);
export const minerAction = (id: string, action: string) => unpack<{status:string;message?:string}>(fetch(
  `/api/miner/${encodeURIComponent(id)}/action`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action_name:action})},
));
export const deleteMiner = (id: string) => unpack<{status:string;message?:string}>(fetch(
  `/api/miner/${encodeURIComponent(id)}/delete`, {method:'POST'},
));
