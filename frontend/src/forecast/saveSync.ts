/** Synchronize a forecast form from server data without discarding pending edits. */
export function syncedForecastDraft<T>(
  stored: T | undefined, current: T | null, dirty: boolean,
): T | null {
  return dirty || stored === undefined ? current : stored;
}

/** Run exactly one new forecast after a successful, enabled settings save.
 * Forecast retrieval errors must not be mistaken for a failed settings save. */
export async function forecastAfterSave<T>(
  enabled: boolean,
  refreshForecast: () => Promise<T>,
): Promise<{attempted: boolean; result?: T; error?: unknown}> {
  if (!enabled) return {attempted: false};
  try { return {attempted: true, result: await refreshForecast()}; }
  catch (error) { return {attempted: true, error}; }
}
