import { Alert, Box, Button, Card, Group, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { IconPlus, IconTrash } from '@tabler/icons-react';
import { readModbusFormula, validateModbusFormula, type ModbusFormula, type ModbusRegister } from './modbusFormulaModel';
import { FormulaEditor } from '../formula/FormulaEditor';

const kindOptions = [
  { value: 'input', label: 'Input (FC04)' },
  { value: 'holding', label: 'Holding (FC03)' },
];
const valueTypes = ['int16', 'uint16', 'int32', 'uint32', 'float32'];
const endianOptions = ['big_endian', 'little_endian'];

/** Generic clickable badge expression editor for externally supplied variables. */
export function ModbusCalculationEditor({ value, onChange, disabled, label, help }: {
  value: string; onChange: (next: string) => void; disabled?: boolean; label?: string; help?: string;
}) {
  const doc = readModbusFormula(value);
  const error = validateModbusFormula(value);
  const commit = (next: ModbusFormula) => onChange(JSON.stringify(next));
  const updateRegister = (id: string, patch: Partial<ModbusRegister>) => {
    commit({ ...doc, registers: doc.registers.map(r => r.id === id ? { ...r, ...patch } : r) });
  };
  const addRegister = () => {
    if (doc.registers.length >= 16) return;
    const r: ModbusRegister = {
      id: 'r-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10), name: '', address: '', register_type: 'input',
      value_type: 'uint32', endian: 'big_endian', factor: 1,
    };
    commit({ ...doc, registers: [...doc.registers, r] });
  };
  return <Stack gap="sm" w="100%">
    <Box>
      <Text fw={600} size="sm">{label || 'Formel'}</Text>
      {help && <Text size="xs" c="dimmed">{help}</Text>}
    </Box>
    <Group justify="space-between" align="center">
      <Text fw={550} size="sm">Modbus-Register</Text>
      <Button leftSection={<IconPlus size={15}/>} size="xs" variant="light"
        onClick={addRegister} disabled={disabled || doc.registers.length >= 16}>Register hinzufügen</Button>
    </Group>
    {doc.registers.map((r, i) => <Card withBorder padding="sm" key={r.id}>
      <Stack gap="xs">
        <Group justify="space-between">
          <Text fw={600} size="xs">Register {i + 1}</Text>
          <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconTrash size={14}/>} disabled={disabled}
            onClick={() => commit({registers: doc.registers.filter(x => x.id !== r.id),
              tokens: doc.tokens.filter(t => typeof t === 'string' || t.type !== 'register' && t.type !== 'variable' || t.id !== r.id)})}>Entfernen</Button>
        </Group>
        <SimpleGrid className="pv-modbus-calculated-grid" cols={{base: 1, sm: 2, md: 3, lg: 6}} spacing="xs">
          <TextInput size="xs" label="Registername" required disabled={disabled} value={r.name}
            onChange={e=>updateRegister(r.id, {name:e.currentTarget.value})} placeholder="z. B. Laden" />
          <TextInput size="xs" label="Adresse (0-basiert)" required disabled={disabled} type="number" min={0} max={65535} value={String(r.address)}
            onChange={e => {
              const address = e.currentTarget.value;
              updateRegister(r.id, { address, ...(!r.name || r.name === String(r.address) ? {name:address} : {}) });
            }}/>
          <Box><Text size="xs" mb={4}>Registerart</Text><select aria-label={`Registerart ${i+1}`} disabled={disabled} value={r.register_type}
              onChange={e=>updateRegister(r.id,{register_type:e.currentTarget.value})}
              className="pv-formula-select">{kindOptions.map(o=><option value={o.value} key={o.value}>{o.label}</option>)}</select></Box>
          <Box><Text size="xs" mb={4}>Datentyp</Text><select aria-label={`Datentyp ${i+1}`} disabled={disabled} value={r.value_type}
              onChange={e=>updateRegister(r.id,{value_type:e.currentTarget.value})}
              className="pv-formula-select">{valueTypes.map(t=><option value={t} key={t}>{t}</option>)}</select></Box>
          <Box><Text size="xs" mb={4}>Byte-Reihenfolge</Text><select aria-label={`Byte-Reihenfolge ${i+1}`} disabled={disabled} value={r.endian}
              onChange={e=>updateRegister(r.id,{endian:e.currentTarget.value})}
              className="pv-formula-select">{endianOptions.map(t=><option value={t} key={t}>{t}</option>)}</select></Box>
          <TextInput size="xs" label="Faktor" required disabled={disabled} type="number" step="any" value={String(r.factor)}
            onChange={e=>updateRegister(r.id,{factor:e.currentTarget.value})}/>
        </SimpleGrid>
      </Stack>
    </Card>)}
    <FormulaEditor
      variables={doc.registers.map(r => ({ id: r.id, name: r.name || String(r.address) || 'Unbenannt' }))}
      tokens={doc.tokens}
      disabled={disabled}
      onChange={tokens => commit({ ...doc, tokens })}
    />
    {error ? <Alert color="orange" title="Formel noch nicht vollständig" py="xs" w="100%">{error}</Alert>
      : <Text size="xs" c="teal">Formel gültig</Text>}
  </Stack>;
}
