import { Box, Checkbox, Grid, TextInput } from '@mantine/core';
import type { SocketDriver, SocketFormValues } from './model';

const span:Record<string,number>={full:12,half:6,third:4,quarter:3};
export function SocketFields({
  driver, values, disabled=false, onChange,
  configuredPassword=false,
}:{
  driver:SocketDriver;
  values:SocketFormValues;
  disabled?:boolean;
  onChange:(name:string,value:string|boolean)=>void;
  configuredPassword?:boolean;
}){
  const fields=driver.fields;
  return <Grid gap="xs">
    {fields.map(field=><Grid.Col key={field.name} span={{base:12,sm:span[field.width||'full']||12}}>
      {field.type==='checkbox'?
        <Box pt="md">
          <Checkbox label={field.label} description={field.help}
            checked={values[field.name]===true} disabled={disabled}
            onChange={e=>onChange(field.name,e.currentTarget.checked)}/>
        </Box>
      :<TextInput label={field.label} size="sm" description={field.help}
        type={field.type==='number'?'number':field.type==='password'?'password':'text'}
        value={String(values[field.name]??'')}
        placeholder={field.type==='password'&&configuredPassword?'Gespeichertes Passwort unverändert':' '}
        min={field.min} max={field.max} step={field.step}
        disabled={disabled}
        autoComplete={field.type==='password'?'new-password':undefined}
        onChange={e=>onChange(field.name,e.currentTarget.value)}
      />}
    </Grid.Col>)}
  </Grid>;
}
