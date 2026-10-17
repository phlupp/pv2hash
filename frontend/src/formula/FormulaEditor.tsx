import { useState } from 'react';
import { Box, Button, Group, Stack, Text, TextInput } from '@mantine/core';
import { IconArrowBackUp } from '@tabler/icons-react';
import { renderToken, type FormulaToken } from './model';

/** Generic formula editor. The caller supplies variables; no knowledge of Modbus. */
export function FormulaEditor({ variables, tokens, onChange, disabled }: {
  variables: Array<{ id: string; name: string }>;
  tokens: FormulaToken[];
  onChange: (tokens: FormulaToken[]) => void;
  disabled?: boolean;
}) {
  const [numberInput, setNumberInput] = useState('0');
  const addToken = (token: FormulaToken) => onChange([...tokens, token]);
  return <Stack gap="sm">
    <Text size="sm" fw={550}>Formel zusammenstellen</Text>
    <Box bg="dark.7" p="sm" style={{borderRadius: 8, minHeight: 56}}>
      <Group gap={5} wrap="wrap">
        {tokens.length ? tokens.map((token, i) =>
          <Button key={i} size="compact-sm" radius="xl" variant="light"
            color={typeof token==='object' && (token.type==='register'||token.type==='variable')?'blue':'gray'}
            disabled={disabled} title="Klicken zum Entfernen"
            onClick={()=>onChange(tokens.filter((_,k)=>k!==i))}>
            {renderToken(token,variables)}</Button>) : <Text size="xs" c="dimmed">Bausteine unten anklicken …</Text>}
      </Group>
    </Box>
    <Group gap={5} wrap="wrap" aria-label="Verfügbare Variablen">
      {variables.map(variable=><Button key={variable.id} size="compact-sm" radius="xl" variant="light" color="blue"
        disabled={disabled || !variable.name || tokens.length >= 96}
        onClick={()=>addToken({type:'variable',id:variable.id})}>{variable.name}</Button>)}
    </Group>
    <Group gap={5} wrap="wrap" aria-label="Rechenoperatoren">
      {['+', '-', '*', '/', '(', ')'].map(op=><Button key={op} size="compact-sm" radius="xl" color="gray" variant="light"
        disabled={disabled || tokens.length >= 96} onClick={()=>addToken(op)}>{op}</Button>)}
    </Group>
    <Group gap="xs" align="end" wrap="wrap">
      <TextInput size="xs" label="Numerischer Wert" value={numberInput} onChange={e=>setNumberInput(e.currentTarget.value)} disabled={disabled} w={138} placeholder="z. B. 1,15"/>
      <Button size="xs" variant="light" disabled={disabled || tokens.length >= 96 || !numberInput.trim() || !Number.isFinite(Number(numberInput.replace(',','.')))}
        onClick={()=>addToken({type:'number',value:Number(numberInput.replace(',','.'))})}>Zahl einfügen</Button>
      <Button size="xs" leftSection={<IconArrowBackUp size={15}/>} color="gray" variant="subtle" disabled={disabled || !tokens.length}
        onClick={()=>onChange(tokens.slice(0,-1))}>Rückgängig</Button>
      <Button size="xs" color="gray" variant="subtle" disabled={disabled || !tokens.length}
        onClick={()=>onChange([])}>Leeren</Button>
    </Group>
    <Text size="xs" c="dimmed">Punkt-vor-Strich und Klammern werden berücksichtigt. Negative Zahlen sind als Konstanten möglich. Antippen eines Formelbausteins entfernt ihn.</Text>
  </Stack>;
}
