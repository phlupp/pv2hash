import type { DataLoggerRange, DataLoggerSeries, DataLoggerStatus, DebugResponse } from './types';

async function request<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  if (!resp.ok) throw new Error(`DataLogger-API: HTTP ${resp.status}`);
  return await resp.json() as T;
}

export type SeriesParams = {
  range: DataLoggerRange;
  end: string | null;
  minerIds: string[];
};
function queryString(params: SeriesParams): string {
  const query = new URLSearchParams({ range: params.range, max_points: '720' });
  if (params.end) query.set('end', params.end);
  if (params.minerIds.length) query.set('miner_ids', params.minerIds.join(','));
  return query.toString();
}

export async function fetchLoggerStatus(): Promise<DataLoggerStatus> {
  return (await request<{ status: string; datalogger: DataLoggerStatus }>('/api/datalogger/status')).datalogger;
}

export async function fetchLoggerSeries(params: SeriesParams): Promise<DataLoggerSeries> {
  return (await request<{ status: string; series: DataLoggerSeries }>(`/api/datalogger/series?${queryString(params)}`)).series;
}

export async function fetchLoggerDebug(params: SeriesParams): Promise<DebugResponse> {
  const q = new URLSearchParams(queryString(params));
  q.delete('max_points');
  q.set('limit', '200');
  return (await request<{ status: string; debug: DebugResponse }>(`/api/datalogger/controller-debug?${q}`)).debug;
}
