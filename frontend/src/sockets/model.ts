export type SocketField = {
  name: string; label: string; type: 'checkbox'|'number'|'text'|'password';
  default: string | number | boolean;
  width?: 'full'|'half'|'third'|'quarter';
  min?: number; max?: number; step?: number;
  help?: string;
};
export type SocketDriver = {
  id: string; label: string; default_name: string; default_host: string;
  fields: SocketField[];
};
export type SocketConfig = {
  id: string; name: string; driver: string; host: string; priority: number;
  monitor_enabled: boolean; control_enabled: boolean;
  settings: Record<string, unknown>;
  password_configured: boolean;
  assignment?: { role?: string; target_id?: string; target_name?: string };
};
export type SocketLive = {
  id: string; key: string; name: string; driver: string; host: string; priority: number;
  monitor_enabled: boolean; control_enabled: boolean; reachable: boolean;
  quality: string; is_on: boolean | null; power_w: number | null; runtime_state: string;
  last_seen: string | null; last_error: string | null; details: Record<string, unknown>;
};
export type NetworkInterface = {ifname:string; cidr:string; label:string};
export type SocketGuiResponse = {
  status:string; drivers:SocketDriver[]; sockets:SocketConfig[];
  discovery_interfaces:NetworkInterface[];
};
export type SocketLiveResponse = {status:string; sockets:SocketLive[]};
export type SocketFormValues = Record<string, string|boolean>;
export type DiscoveryResult = {
  host:string; name?:string; friendly_name?:string; device_name?:string;
  port?:number; is_on?:boolean|null; power_w?:number|null;
  existing_socket_id?:string; existing_socket_name?:string;
};
export type DiscoveryResponse = {
  status:string; message?:string; results:DiscoveryResult[]; cidrs?:string[];
};

export function socketFields(driver: SocketDriver, creating: boolean): SocketField[] {
  return [
    {name:'driver',label:'Treiber',type:'text',default:driver.id,width:'quarter'},
    {name:'name',label:'Name',type:'text',default:driver.default_name,width:'half'},
    {name:'host',label:'Host/IP',type:'text',default:driver.default_host,width:'quarter'},
    ...(!creating ? [
      {name:'priority',label:'Priorität',type:'number' as const,default:100,width:'quarter' as const},
      {name:'monitor_enabled',label:'Verbindung',type:'checkbox' as const,default:true,width:'quarter' as const},
      {name:'control_enabled',label:'Automatik',type:'checkbox' as const,default:false,width:'quarter' as const},
    ] : []),
    ...driver.fields,
  ];
}
export function initialSocketValues(driver: SocketDriver, config?: SocketConfig): SocketFormValues {
  const values:SocketFormValues={};
  for(const field of socketFields(driver,!config)) {
    const saved = config
      ? ['name','host','priority','monitor_enabled','control_enabled','driver'].includes(field.name)
        ? config[field.name as keyof SocketConfig]
        : config.settings[field.name]
      : undefined;
    values[field.name] = field.type === 'checkbox'
      ? Boolean(saved ?? field.default)
      : String(saved ?? field.default ?? '');
  }
  return values;
}
export function validateSocket(driver: SocketDriver, values: SocketFormValues): string|null {
  if(!String(values.name||'').trim())return 'Bitte einen Socket-Namen eingeben.';
  if(driver.id==='tasmota_http' && !String(values.host||'').trim())
    return 'Bitte eine Host-/IP-Adresse für Tasmota eingeben.';
  for(const field of socketFields(driver,false)) {
    if(field.type!=='number'|| !(field.name in values))continue;
    const raw=String(values[field.name]??'');
    const num=Number(raw);
    if(!raw.trim()||!Number.isFinite(num)|| (field.min!==undefined&&num<field.min)
      ||(field.max!==undefined&&num>field.max))return field.label+': ungültiger Zahlenwert.';
    if(['priority','port','relay'].includes(field.name)&&!Number.isInteger(num))
      return field.label+': bitte eine ganze Zahl eingeben.';
  }
  return null;
}
export function serializeSocketValues(driver:SocketDriver,values:SocketFormValues,existing:boolean):FormData {
  const form=new FormData();
  for(const field of socketFields(driver,!existing)) {
    if(field.type==='checkbox') {
      if(values[field.name]===true)form.set(field.name,'on');
    }else if(field.type==='password'&&existing&& !values[field.name]) {
      form.set(field.name,'__KEEP__');
    }else {
      form.set(field.name,String(values[field.name]??''));
    }
  }
  return form;
}
