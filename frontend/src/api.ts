export interface StatusResponse {
  snapshot?: {
    grid_power_w?: number | null;
    pv_power_w?: number | null;
    house_power_w?: number | null;
    battery_soc_pct?: number | null;
    battery_charge_power_w?: number | null;
    battery_discharge_power_w?: number | null;
    source?: string | null;
    quality?: string | null;
    updated_at?: string | null;
  } | null;
  miners?: Array<{
    id: string;
    name?: string;
    power_w?: number | null;
    current_hashrate_ghs?: number | null;
    reachable?: boolean;
    runtime_state?: string;
  }>;
  last_decision?: string | null;
}

export interface VersionResponse {
  version_full?: string;
  version?: string;
  update_available?: boolean;
}

async function apiGet<T>(url: string): Promise<T> {
  const response = await fetch(url, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`Backend antwortet mit HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export const getStatus = () => apiGet<StatusResponse>('/api/status');
export const getVersion = () => apiGet<VersionResponse>('/api/ui/versionstatus');
