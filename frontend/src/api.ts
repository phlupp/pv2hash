export interface StatusResponse {
  config?: {
    source?: { name?: string };
    battery?: { enabled?: boolean; name?: string };
    pv?: { name?: string };
  };
  snapshot?: {
    grid_power_w?: number | null;
    pv_power_w?: number | null;
    house_power_w?: number | null;
    battery_soc_pct?: number | null;
    battery_charge_power_w?: number | null;
    battery_discharge_power_w?: number | null;
    battery_is_charging?: boolean;
    battery_is_discharging?: boolean;
    battery_is_active?: boolean;
    battery_quality?: string | null;
    source?: string | null;
    quality?: string | null;
    updated_at?: string | null;
  } | null;
  miners?: Array<{
    id: string;
    name?: string;
    driver?: string;
    power_w?: number | null;
    current_hashrate_ghs?: number | null;
    reachable?: boolean;
    runtime_state?: string;
  }>;
  last_decision?: string | null;
}

export interface DashboardMiner {
  id: string;
  name: string;
  priority: number;
  host: string;
  host_gui_url?: string | null;
  profile: string;
  power_text: string;
  hashrate_text: string;
  monitor_enabled: boolean;
  control_enabled: boolean;
  reachable: boolean;
  is_running: boolean;
}

export interface DashboardLiveResponse {
  status: 'ok';
  cards: {
    grid?: { value: string; class: string; hint: string };
    pv?: { power_w: number | null; quality: string; enabled: boolean; source?: string; updated_at?: string | null };
    source?: { label: string; meta: string; quality: string };
    miners?: { power: string; meta: string };
    battery?: { value: string; class: string; state: string };
    host?: { cpu: string; ram: string };
    controller: {
      policy_mode?: string;
      distribution_mode?: string;
      summary: string;
      last_switch: string;
      last_event_meta: string;
      ring_state: 'waiting' | 'ready' | 'disabled' | string;
      ring_progress: number;
      ring_inner: string;
      ring_hint: string;
    };
  };
  miners: DashboardMiner[];
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
export const getDashboardStatus = () => apiGet<DashboardLiveResponse>('/api/dashboard/status');
export const getVersion = () => apiGet<VersionResponse>('/api/ui/versionstatus');
