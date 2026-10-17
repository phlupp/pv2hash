/** Centralized semantic colors for DataLogger ECharts series (no per-page inline colors). */
export const chartColors = {
  grid: '#6dcff6',
  pv: '#f8cb72',
  miningPower: '#aa8bff',
  batteryCharge: '#42dba3',
  batteryDischarge: '#efae70',
  soc: '#d5e1f0',
  hashrate: '#6dcff6',
  temperature: '#f8cb72',
  asicMin: '#42dba3',
  asicMax: '#f48ba6',
  event: 'rgba(248, 203, 114, 0.35)',
  selectedEvent: '#f8cb72',
  axis: '#aeb8c7',
  gridLine: 'rgba(255,255,255,0.075)',
} as const;
