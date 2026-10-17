import { validateExpressionTokens, type FormulaToken } from '../formula/model.ts';

/** Specialized Modbus register config wrapping the generic expression editor. */
export type ModbusRegister = {
  id: string; name: string; address: number | string;
  register_type: string; value_type: string; endian: string; factor: number | string;
};
export type ModbusFormula = { registers: ModbusRegister[]; tokens: FormulaToken[] };

export function readModbusFormula(value: string): ModbusFormula {
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const result = parsed as Partial<ModbusFormula>;
      if (Array.isArray(result.registers) && Array.isArray(result.tokens)) {
        return { registers: result.registers, tokens: result.tokens };
      }
    }
  } catch { /* incomplete JSON */ }
  return { registers: [], tokens: [] };
}

export function validateModbusFormula(value: string): string | null {
  let doc: ModbusFormula;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return 'Ungültige Formeldaten.';
    doc = parsed as ModbusFormula;
  } catch { return 'Ungültige Formeldaten.'; }
  const { registers, tokens } = doc;
  if (!Array.isArray(registers) || registers.length < 2 || registers.length > 16) {
    return 'Mindestens zwei, höchstens 16 Register konfigurieren.';
  }
  const ids = new Set<string>();
  const names = new Set<string>();
  for (const r of registers) {
    if (!r || typeof r.id !== 'string' || !r.id || ids.has(r.id)) return 'Registerkennungen müssen eindeutig sein.';
    const name = typeof r.name === 'string' ? r.name.trim() : '';
    if (!name || names.has(name.toLowerCase())) {
      return 'Register benötigen eindeutige Namen.';
    }
    if (r.address === null || r.address === undefined || String(r.address).trim() === ''
      || !Number.isInteger(Number(r.address)) || Number(r.address) < 0 || Number(r.address) > 65535) {
      return 'Registeradresse muss zwischen 0 und 65535 liegen.';
    }
    if (!Number.isFinite(Number(r.factor)) || Number(r.factor) === 0) return 'Registerfaktor darf nicht 0 sein.';
    ids.add(r.id); names.add(name.toLowerCase());
  }
  const validation = validateExpressionTokens(tokens, ids);
  if (validation.error) return validation.error;
  if (validation.used.size < 2) return 'Die Formel muss mindestens zwei Register verwenden.';
  return null;
}
