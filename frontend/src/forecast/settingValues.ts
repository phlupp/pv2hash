/** Keep numeric forecast cadence independent of provider string selectors. */
export function forecastSelectValue(name: string, value: string | null): string | number {
  return name === 'resolution_minutes' ? Number(value ?? 15) : value ?? '';
}
