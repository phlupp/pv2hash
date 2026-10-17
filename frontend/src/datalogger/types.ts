export type DataLoggerRange = '1h' | '3h' | '6h' | '12h' | '24h' | '7d';
export type DataLoggerPoint = {
  ts: string;
  grid_power_w: number | null;
  battery_soc_pct: number | null;
  battery_charge_power_w: number | null;
  battery_discharge_power_w: number | null;
  miner_power_w_total: number | null;
  miner_hashrate_ghs_total: number | null;
  miner_temp_c: number | null;
  miner_temp_asic_min_c: number | null;
  miner_temp_asic_max_c: number | null;
  source_quality?: string | null;
  battery_quality?: string | null;
};

export type MinerChoice = {
  id: string;
  key?: string;
  name: string;
  driver?: string;
};

export type ControllerMarker = {
  event_id: number;
  ts: string;
  at?: string;
  miner_id?: string;
  miner_name?: string;
  old_profile?: string;
  new_profile?: string;
  reason_code?: string;
  reason_text?: string;
  direction?: 'up' | 'down' | 'neutral';
  label?: string;
  flags?: string[];
  grid_power_w?: number | null;
  battery_soc_pct?: number | null;
  battery_direction?: string | null;
  battery_charge_power_w?: number | null;
  battery_discharge_power_w?: number | null;
};

export type DataLoggerSeries = {
  range: DataLoggerRange;
  start: string;
  end: string;
  is_live: boolean;
  raw_count: number;
  point_count: number;
  marker_count: number;
  max_points: number;
  selected_miner_ids: string[];
  miners: MinerChoice[];
  points: DataLoggerPoint[];
  markers: ControllerMarker[];
};

export type DataLoggerStatus = {
  enabled: boolean;
  interval_seconds: number;
  retention_days: number;
  sample_count: number;
  controller_event_count: number;
  controller_debug_event_count: number;
  newest_sample_at: string | null;
  last_error: string | null;
};

export type DebugEvent = {
  event_id: number;
  at: string;
  event_type: string;
  miner_name?: string;
  miner_id?: string;
  current_profile?: string;
  requested_profile?: string;
  effective_profile?: string;
  reason_text?: string;
  reason_code?: string;
  grid_power_w?: number | null;
  battery_soc_pct?: number | null;
  flags?: string[];
  min_switch_remaining_s?: number | null;
};

export type DebugResponse = {
  event_count: number;
  events: DebugEvent[];
  retention_hours: number;
};
