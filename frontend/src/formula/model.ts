/** Shared formula token format; usable for Modbus or any source of variables. */
export type FormulaToken = string | { type: 'variable' | 'register'; id: string } | { type: 'number'; value: number };

export function renderToken(token: FormulaToken, variables: Array<{ id: string; name: string }>): string {
  if (typeof token === 'string') return token;
  if (token.type === 'number') return String(token.value).replace('.', ',');
  return variables.find(x => x.id === token.id)?.name || `? ${token.id}`;
}

export function validateExpressionTokens(tokens: FormulaToken[], available: Set<string>): { error: string | null; used: Set<string> } {
  const used = new Set<string>();
  if (!Array.isArray(tokens) || !tokens.length || tokens.length > 96) {
    return { error: 'Formel muss 1–96 Bausteine enthalten.', used };
  }
  let expectValue = true;
  let depth = 0;
  for (const token of tokens) {
    if (typeof token === 'string') {
      if (token === '(' && expectValue) { depth++; if (depth > 12) return {error:'Zu viele Klammern.', used}; continue; }
      if (token === ')' && !expectValue && depth > 0) { depth--; continue; }
      if (['+', '-', '*', '/'].includes(token) && !expectValue) { expectValue = true; continue; }
      return { error: 'Unvollständige Formel oder ungültiger Operator.', used };
    }
    if (!token || expectValue === false) return { error: 'Zwischen zwei Werten fehlt ein Operator.', used };
    if ((token.type === 'variable' || token.type === 'register') && available.has(token.id)) used.add(token.id);
    else if (token.type !== 'number' || typeof token.value !== 'number' || !Number.isFinite(token.value)) {
      return { error: 'Ungültiger Variablenbezug oder Zahlenwert.', used };
    }
    expectValue = false;
  }
  return { error: expectValue || depth !== 0 ? 'Formel oder Klammerung ist unvollständig.' : null, used };
}
