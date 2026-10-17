import type {SocketGuiResponse,SocketLiveResponse,DiscoveryResponse} from './model';

export async function socketApiJson<T extends {status?:string;message?:string}>(promise:Promise<Response>):Promise<T>{
  const r=await promise;
  if(!r.headers.get('content-type')?.includes('application/json'))
    throw new Error(`Socket-API: HTTP ${r.status} – keine JSON-Antwort`);
  let x:T;
  try { x=await r.json() as T; }
  catch {throw new Error(`Socket-API: HTTP ${r.status} – fehlerhafte JSON-Antwort`);}
  if(!r.ok||x.status==='error')throw new Error(x.message||`Socket-API: HTTP ${r.status}`);
  return x;
}
const get=<T extends {status?:string;message?:string}>(path:string)=>socketApiJson<T>(fetch(path,{cache:'no-store',headers:{Accept:'application/json'}}));
const post=(path:string,body:FormData)=>socketApiJson<{status:string;message?:string;socket_id?:string}>(
  fetch(path,{method:'POST',body}));
export const fetchSocketGui=()=>get<SocketGuiResponse>('/api/sockets/gui');
export const fetchSocketStatus=()=>get<SocketLiveResponse>('/api/sockets/status');
export const addSocket=(form:FormData)=>post('/api/sockets/add',form);
export const saveSocket=(id:string,form:FormData)=>post(`/api/socket/${encodeURIComponent(id)}/config`,form);
export const deleteSocket=(id:string)=>socketApiJson<{status:string;message?:string}>(
  fetch(`/api/socket/${encodeURIComponent(id)}/delete`,{method:'POST'}));
export const switchSocket=(id:string,action:'on'|'off'|'reboot')=>socketApiJson<{status:string;message?:string}>(
  fetch(`/api/socket/${encodeURIComponent(id)}/switch`,{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action}),
  }));
export const discoverSockets=(form:FormData)=>postDiscovery('/api/sockets/discover/tasmota',form);
const postDiscovery=(path:string,form:FormData)=>socketApiJson<DiscoveryResponse>(fetch(path,{method:'POST',body:form}));
