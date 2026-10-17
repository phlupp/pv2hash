import type {ExporterResponse,ExporterStatusResponse,SettingsResponse} from './types';
export async function configJson<T extends {status?:string;message?:string}>(response:Promise<Response>):Promise<T>{
  const r=await response;
  if(!r.headers.get('content-type')?.includes('application/json'))throw new Error(`HTTP ${r.status}: keine JSON-Antwort`);
  let data:T;
  try{data=await r.json() as T;}catch{throw new Error(`HTTP ${r.status}: ungültige JSON-Antwort`);}
  if(!r.ok||data.status==='error')throw new Error(data.message||`HTTP ${r.status}`);
  return data;
}
const get=<T extends {status?:string;message?:string}>(path:string)=>configJson<T>(fetch(path,{cache:'no-store',headers:{Accept:'application/json'}}));
const post=<T extends {status?:string;message?:string}>(path:string,payload:unknown)=>configJson<T>(fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));
export const getSettings=()=>get<SettingsResponse>('/api/settings/model');
export const saveSettings=(data:Record<string,string|boolean>)=>post<SettingsResponse>('/api/settings/config',data);
export const geocodeLocation=(address:string)=>post<{status:string;message?:string;location:{lat:number;lon:number;display_name?:string}}>('/api/location/geocode',{address});
export const getExporters=()=>get<ExporterResponse>('/api/exporters/config');
export const getExporterStatus=()=>get<ExporterStatusResponse>('/api/exporters/status');
export const saveExporter=(data:{id?:string;type:string;name:string;enabled:boolean;settings:Record<string,string|boolean>})=>post<ExporterResponse>('/api/exporters/config',data);
export const testExporter=(id:string)=>post<{status:string;message?:string}>(`/api/exporters/config/${encodeURIComponent(id)}/test`,{});
export const deleteExporter=(id:string)=>configJson<ExporterResponse>(fetch(`/api/exporters/config/${encodeURIComponent(id)}`,{method:'DELETE'}));
