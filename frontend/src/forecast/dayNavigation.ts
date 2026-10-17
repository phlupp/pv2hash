/** Navigating to today always restores the live endpoint (no fixed date). */
export function dayShift(day: string, offset: number): string {
  // UTC noon avoids daylight-saving date boundaries while navigating days.
  const date = new Date(day + 'T12:00:00Z');
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

export function daySelectionAfterMove(
  visibleDay: string, siteToday: string, offset: number,
): string | null {
  const target = dayShift(visibleDay, offset);
  return target === siteToday ? null : target;
}

/** 30 s live refresh like the Data Logger; historical forecasts are immutable. */
export const FORECAST_LIVE_REFRESH_MS = 30_000;
export function forecastDayRefreshInterval(
  selectedDay: string | null, data?: { today: string },
): number | false {
  if (selectedDay === null || !data) return FORECAST_LIVE_REFRESH_MS;
  return selectedDay >= data.today ? FORECAST_LIVE_REFRESH_MS : false;
}
