import { Box, Checkbox, Grid, Select, Text, TextInput } from '@mantine/core';
import type { MinerField, MinerValues } from './model';

const widths = {full:12,half:6,third:4,quarter:3,auto:4};

export function MinerFields({
  fields, values, disabled, onChange, existing = true,
}: {
  fields: MinerField[];
  values: MinerValues;
  disabled?: boolean;
  existing?: boolean;
  onChange: (field: MinerField, value: string | boolean) => void;
}) {
  return <Grid gap="xs">
    {fields.map(field => {
      if (!field.name) return null;
      const span = widths[field.layout?.width ?? 'full'] || 12;
      const locked = !!disabled || !!field.read_only || field.value_known === false;
      const value = values[field.name] ?? (field.type === 'checkbox' ? false : '');
      const label = field.unit ? `${field.label} (${field.unit})` : field.label;
      return <Grid.Col key={field.name} span={{base:12,sm:span}}>
        {field.type === 'checkbox' ? (
          <Box pt="sm"><Checkbox checked={value === true} label={label}
            description={field.help || (field.read_only ? 'Vom Treiber fest vorgegeben.' : undefined)}
            disabled={locked} onChange={e=>onChange(field,e.currentTarget.checked)} /></Box>
        ) : field.type === 'select' ? (
          <Select label={label} description={field.help} size="sm" required={field.required}
            disabled={locked} searchable={(field.options?.length ?? 0)>6} allowDeselect={false}
            data={(field.options??[]).map(o=>({value:String(o.value),label:o.label}))}
            value={String(value)||null} placeholder={field.placeholder || 'Bitte wählen …'}
            onChange={v=>onChange(field,v??'')} />
        ) : ['number','password','text'].includes(field.type) ? (
          <TextInput label={label} description={field.help ||
             (field.read_only ? 'Vom Treiber fest vorgegeben.' : field.value_known === false ? 'Aktueller Gerätewert unbekannt.' : undefined)}
            required={field.required} disabled={locked} size="sm" type={field.type}
            min={field.min} max={field.max} step={field.step}
            value={String(value)} placeholder={field.type === 'password' ? (existing ? 'Unverändert lassen' : 'Passwort eingeben') : field.placeholder}
            onChange={e=>onChange(field,e.currentTarget.value)} />
        ) : <Text c="orange" size="xs">Unbekannter Feldtyp: {field.type}</Text>}
      </Grid.Col>;
    })}
  </Grid>;
}
