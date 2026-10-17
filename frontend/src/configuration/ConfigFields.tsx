import { Box, Checkbox, Grid, Select, Text, TextInput } from '@mantine/core';
import type {ConfigField,ConfigValues} from './types';
const widths:Record<string,number>={full:12,half:6,third:4,quarter:3,auto:4};
export function ConfigFields({fields,values,busy,onChange,existing=true}:{
  fields:ConfigField[];values:ConfigValues;busy?:boolean;existing?:boolean;
  onChange:(name:string,value:string|boolean)=>void;
}){
  return <Grid gap="xs">
    {fields.map(field=>{
      const val=values[field.name]??(field.type==='checkbox'?false:'');
      const disabled=Boolean(field.disabled||field.read_only||busy);
      const span=widths[field.layout?.width||'full']||12;
      return <Grid.Col key={field.name} span={{base:12,sm:span}}>
        {field.type==='checkbox'
          ? <Box pt="sm"><Checkbox label={field.label} description={field.help} checked={val===true}
                disabled={disabled} onChange={e=>onChange(field.name,e.currentTarget.checked)}/></Box>
          : field.type==='select'
            ? <Select label={field.label} description={field.help} required={field.required}
                size="sm" disabled={disabled}
                data={(field.options||[]).map(o=>({value:String(o.value),label:o.label}))}
                searchable={(field.options?.length||0)>5} allowDeselect={false}
                value={String(val)||null} onChange={v=>onChange(field.name,v??'')}/>
            : ['text','number','password','url'].includes(field.type)
              ? <TextInput label={field.label} description={field.help} required={field.required}
                  size="sm" disabled={disabled} type={field.type==='url'?'url':field.type}
                  value={String(val)} min={field.min} max={field.max} step={field.step}
                  placeholder={field.type==='password'&&existing?'Leer lassen = unverändert':field.placeholder}
                  autoComplete={field.type==='password'?'new-password':undefined}
                  onChange={e=>onChange(field.name,e.currentTarget.value)}/>
              : <Text c="orange" size="xs">Unbekannter Feldtyp: {field.type}</Text>}
      </Grid.Col>;
    })}
  </Grid>;
}
