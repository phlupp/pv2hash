export type ConfigField = {
  name: string;
  label: string;
  type: string;
  value?: string | number | boolean | null;
  default?: string | number | boolean | null;
  help?: string;
  required?: boolean;
  disabled?: boolean;
  read_only?: boolean;
  min?: number;
  max?: number;
  step?: string | number;
  placeholder?: string;
  layout?: { width?: string };
  options?: Array<{ value: string | number; label: string }>;
};
export type ConfigSection = {
  id: string; title: string; subtitle?: string; description?: string; fields: ConfigField[];
};
export type SettingsModel = {sections: ConfigSection[]};
export type SettingsResponse = {status:string;message?:string;instance_name?:string;model:SettingsModel};
export type ConfigValues = Record<string,string|boolean>;
export type ExporterType = {type:string;label:string;fields:ConfigField[]};
export type ExporterEntry = {
  id:string;name:string;type:string;enabled:boolean;configured:boolean;test_ok:boolean;
  tested_at?:string|null;schema:{label:string;fields:ConfigField[]};
};
export type ExporterResponse = {status:string;message?:string;exporters:ExporterEntry[];types:ExporterType[]};
export type ExporterStatus = {
  id:string;enabled:boolean;adapter_available?:boolean;streams?:Record<string,{last_error?:string;last_success_at?:string}>;
  exported_sample_at?:string;backlog_seconds?:number;
};
export type ExporterStatusResponse={status:string;exporters:ExporterStatus[]};

export function fieldValues(fields:ConfigField[]):ConfigValues {
  return Object.fromEntries(fields.map(f=>[f.name, f.type==='checkbox'?Boolean(f.value??f.default):String(f.value??f.default??'')]));
}
export function validateConfig(fields:ConfigField[],values:ConfigValues,existing=true):string|null {
  for(const f of fields){
    if(f.disabled||f.read_only)continue;
    const v=values[f.name];
    const raw=String(v??'');
    if(f.required && (raw.trim()===''||f.type==='checkbox'&&v!==true)) {
      if(!(existing&&f.type==='password'&&raw===''))return f.label+': Pflichtfeld.';
    }
    if(f.type==='number'&&raw.trim()){
      const n=Number(raw);
      if(!Number.isFinite(n)||(f.min!==undefined&&n<f.min)||(f.max!==undefined&&n>f.max))
        return f.label+': ungültiger Zahlenwert.';
    }
  }
  return null;
}
