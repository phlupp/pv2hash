import type { ControllerMarker } from './types';

/** Bin applied controller events along the selected time window.
 * The full event list stays in the API payload; this is only a display transform.
 */
export type EventCluster = {
  index: number;
  events: ControllerMarker[];
  latest: ControllerMarker;
  percentage: number;
};

export function clusterEvents(
  markers: ControllerMarker[],
  start: string,
  end: string,
  maxBins = 26,
): EventCluster[] {
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  const span = endMs - startMs;
  if (!Number.isFinite(span) || span <= 0 || maxBins <= 0) return [];
  const bins = new Map<number, ControllerMarker[]>();
  for (const marker of markers) {
    const ms = Date.parse(marker.ts || marker.at || '');
    if (!Number.isFinite(ms) || ms < startMs || ms > endMs) continue;
    const position = Math.max(0, Math.min(maxBins - 1, Math.floor((ms - startMs) / span * maxBins)));
    const list = bins.get(position) || [];
    list.push(marker);
    bins.set(position, list);
  }
  return [...bins.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([index, events]) => ({
      index,
      events,
      latest: events[events.length - 1],
      percentage: Math.max(3, Math.min(97, ((index + 0.5) / maxBins) * 100)),
    }));
}
