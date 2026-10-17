/** Safe, typed view of the existing read-only socket-assignment API. */
export type MinerSocketAssignment = {
  miner_name: string;
  show_section: boolean;
  socket: {socket_id: string; mode: 'measure_only' | 'switching'; enabled: boolean;
    startup_delay_seconds: number; power_off_after_off_seconds: number};
  socket_name: string;
  socket_missing: boolean;
  options: Array<{id:string;label:string;driver:string;host:string}>;
  runtime: {power_w?:number|null; reachable?:boolean; is_on?:boolean|null; details?:Record<string, unknown>};
  workflow: {state?:string; message?:string; startup_remaining_text?:string; power_off_remaining_text?:string};
  min_power_off_after_off_seconds: number;
};
export type SocketAssignmentResponse = {status: string; miners:Record<string,MinerSocketAssignment>};
export type SocketAssignmentValues = {
  socket_id: string; mode: 'measure_only'|'switching'; startup_delay_seconds: string; power_off_after_off_seconds: string;
};
export function socketValuesFrom(item?:MinerSocketAssignment):SocketAssignmentValues {
  const s=item?.socket;
  return {socket_id:s?.socket_id||'',mode:s?.mode==='switching'?'switching':'measure_only',
    startup_delay_seconds:String(s?.startup_delay_seconds??60),
    power_off_after_off_seconds:String(s?.power_off_after_off_seconds??3600)};
}
export function validateSocketAssignment(v:SocketAssignmentValues,minPowerOff=600):string|null {
  if(!v.socket_id)return null;
  if(v.mode!=='measure_only' && v.mode!=='switching')return 'Ungültiger Steckdosenmodus.';
  for(const [name,raw,min] of [
    ['Einschaltverzögerung',v.startup_delay_seconds,0],
    ['Ausschaltverzögerung',v.power_off_after_off_seconds,minPowerOff],
  ] as const) {
    if (!raw.trim() || !Number.isSafeInteger(Number(raw)) || Number(raw)<min) {
      return `${name}: bitte eine ganze Zahl ab ${min} Sekunden eingeben.`;
    }
  }
  return null;
}
export function setSocketFormValues(form:FormData,v:SocketAssignmentValues):void {
  form.set('socket.socket_id',v.socket_id);
  form.set('socket.mode',v.mode);
  form.set('socket.startup_delay_seconds',v.startup_delay_seconds);
  form.set('socket.power_off_after_off_seconds',v.power_off_after_off_seconds);
}
export function formatSocketPower(value:unknown):string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toLocaleString('de-DE',{maximumFractionDigits:1})} W`:'—';
}
export function formatSocketEnergy(value:unknown):string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toLocaleString('de-DE',{maximumFractionDigits:3})} kWh`:'—';
}
