/** Forecast APIs and persistent storage use W; the chart consistently displays kW. */
export function wattsToKw(watts: number): number {
  return watts / 1000;
}

export function formatKw(kilowatts: number): string {
  return `${kilowatts.toLocaleString('de-DE', { maximumFractionDigits: 2 })} kW`;
}

export function formatKwTick(kilowatts: number): string {
  return kilowatts.toLocaleString('de-DE', { maximumFractionDigits: 2 });
}
