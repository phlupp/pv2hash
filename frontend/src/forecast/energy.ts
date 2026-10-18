/** Render server-calculated daily energy; never represent missing data as zero. */
export type EnergySummary = {
  kwh: number | null; coverage_hours: number; day_hours: number; complete: boolean;
};

export function energyValue(value: number | null): string {
  return value === null ? '—' : value.toLocaleString('de-DE', {
    minimumFractionDigits: 1, maximumFractionDigits: 2,
  }) + ' kWh';
}

export function coverageText(energy: EnergySummary, isForecast: boolean, isToday: boolean, isFuture: boolean): string {
  if (energy.kwh === null) {
    return isForecast ? 'Keine Prognoseintervalle verfügbar' :
      isFuture ? 'Noch keine Erzeugung gemessen' : 'Keine vollständigen Messintervalle';
  }
  if (isForecast) return energy.complete ? 'Prognose für den ganzen Tag' :
    'Teilprognose · nicht alle Stunden verfügbar';
  if (isToday) return 'Bisher gemessen · nur vollständige LIVE-Messintervalle';
  return energy.complete ? 'Gemessene Tagesenergie' : 'Teilwert · Messdaten unvollständig';
}
