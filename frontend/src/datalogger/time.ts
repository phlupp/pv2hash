import type { DataLoggerRange } from './types';

export const RANGES: readonly DataLoggerRange[] = ['1h', '3h', '6h', '12h', '24h', '7d'];
export const RANGE_MS: Record<DataLoggerRange, number> = {
  '1h': 3_600_000,
  '3h': 10_800_000,
  '6h': 21_600_000,
  '12h': 43_200_000,
  '24h': 86_400_000,
  '7d': 604_800_000,
};

export function previousWindowEnd(end: string, range: DataLoggerRange): string {
  const time = Date.parse(end);
  if (!Number.isFinite(time)) throw new Error('Ungültiges Zeitfenster');
  return new Date(time - RANGE_MS[range]).toISOString();
}

export function nextWindowEnd(end: string, range: DataLoggerRange, nowMs = Date.now()): string | null {
  const time = Date.parse(end);
  if (!Number.isFinite(time)) throw new Error('Ungültiges Zeitfenster');
  const next = time + RANGE_MS[range];
  return next >= nowMs - 5_000 ? null : new Date(next).toISOString();
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'medium' })
    : '—';
}
export function formatTime(iso: string | null | undefined, range: DataLoggerRange = '1h'): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return '—';
  const opts: Intl.DateTimeFormatOptions = range === '7d'
    ? { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit', ...(range === '1h' ? { second: '2-digit' } : {}) };
  return date.toLocaleString('de-DE', opts);
}
export function formatUnit(value: number | null | undefined, unit = ''): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${value.toLocaleString('de-DE', { maximumFractionDigits: Math.abs(value) < 100 ? 1 : 0 })}${unit}`;
}
