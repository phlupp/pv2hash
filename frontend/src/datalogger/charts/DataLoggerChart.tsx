import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import type { ControllerMarker, DataLoggerPoint, DataLoggerSeries } from '../types';
import { chartColors as col } from './palette';
import { clusterEvents } from '../cluster';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, MarkLineComponent, CanvasRenderer]);

type ChartKind = 'energy' | 'battery' | 'mining';
type Axis = 'y' | 'y1' | 'y2';
type LineDef = { name: string; key: keyof DataLoggerPoint; color: string; axis: Axis; negative?: boolean };
const LINES: Record<ChartKind, LineDef[]> = {
  energy: [
    { name: 'Netz (+ Bezug / − Einspeisung)', key: 'grid_power_w', color: col.grid, axis: 'y' },
    { name: 'Miner W', key: 'miner_power_w_total', color: col.miningPower, axis: 'y' },
    { name: 'Batterie lädt W', key: 'battery_charge_power_w', color: col.batteryCharge, axis: 'y' },
    { name: 'Batterie entlädt W', key: 'battery_discharge_power_w', color: col.batteryDischarge, axis: 'y' },
  ],
  battery: [
    { name: 'Ladeleistung W', key: 'battery_charge_power_w', color: col.batteryCharge, axis: 'y' },
    { name: 'Entladeleistung W', key: 'battery_discharge_power_w', color: col.batteryDischarge, axis: 'y', negative: true },
    { name: 'SOC %', key: 'battery_soc_pct', color: col.soc, axis: 'y1' },
  ],
  mining: [
    { name: 'Minerleistung W', key: 'miner_power_w_total', color: col.miningPower, axis: 'y' },
    { name: 'Hashrate GH/s', key: 'miner_hashrate_ghs_total', color: col.hashrate, axis: 'y1' },
    { name: 'Temperatur °C', key: 'miner_temp_c', color: col.temperature, axis: 'y2' },
    { name: 'ASIC min °C', key: 'miner_temp_asic_min_c', color: col.asicMin, axis: 'y2' },
    { name: 'ASIC max °C', key: 'miner_temp_asic_max_c', color: col.asicMax, axis: 'y2' },
  ],
};

function value(row: DataLoggerPoint, line: LineDef): number | null {
  const raw = row[line.key];
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null;
  return line.negative ? -Math.abs(raw) : raw;
}

function makeOptions(kind: ChartKind, series: DataLoggerSeries, selectedEventId: number | null): EChartsOption {
  const points = series.points;
  const lines = LINES[kind].filter(line => {
    if (kind === 'mining' && ['miner_temp_asic_min_c', 'miner_temp_asic_max_c'].includes(line.key) && series.selected_miner_ids.length !== 1) return false;
    return points.some(point => value(point, line) !== null);
  });
  const representatives = clusterEvents(series.markers, series.start, series.end).map(cluster => cluster.latest);
  const chosen = series.markers.find(marker => marker.event_id === selectedEventId);
  // Selected individual event stays visible even if it belongs to a condensed cluster.
  const markerList = chosen && !representatives.some(m => m.event_id === chosen.event_id)
    ? [...representatives, chosen] : representatives;
  const grid = { left: 58, right: kind === 'mining' ? 82 : (kind === 'battery' ? 57 : 17), top: 55, bottom: 39, containLabel: false };
  const axisBase = {
    axisLabel: { color: col.axis, fontSize: 10 },
    axisLine: { lineStyle: { color: col.gridLine } },
    splitLine: { lineStyle: { color: col.gridLine } },
    nameTextStyle: { color: col.axis, fontSize: 10 },
  };
  const secondary = { ...axisBase, position: 'right' as const, splitLine: { show: false } };
  const yAxis: EChartsOption['yAxis'] = [
    { ...axisBase, type: 'value', name: 'W', nameLocation: 'end' },
    {
      ...secondary, type: 'value', name: kind === 'battery' ? '%' : 'GH/s',
      show: kind !== 'energy', min: kind === 'battery' ? 0 : undefined,
      max: kind === 'battery' ? 100 : undefined,
    },
    { ...secondary, type: 'value', name: '°C', show: kind === 'mining' && lines.some(l => l.axis === 'y2'), offset: 41 },
  ];
  const markLines = markerList.map((event: ControllerMarker) => ({
    xAxis: Date.parse(event.ts || event.at || ''),
    lineStyle: {
      color: event.event_id === selectedEventId ? col.selectedEvent : col.event,
      type: 'dashed' as const,
      width: event.event_id === selectedEventId ? 2 : 1,
    },
  })).filter(m => Number.isFinite(m.xAxis));

  const charts = lines.map((line, index) => ({
    id: line.key,
    name: line.name,
    type: 'line' as const,
    yAxisIndex: line.axis === 'y' ? 0 : line.axis === 'y1' ? 1 : 2,
    data: points.map(point => [Date.parse(point.ts), value(point, line)]),
    showSymbol: false,
    connectNulls: false,
    animation: false,
    lineStyle: { width: 1.8, color: line.color },
    itemStyle: { color: line.color },
    emphasis: { focus: 'series' as const },
    ...(index === 0 && markLines.length ? {
      markLine: {
        silent: true,
        symbol: 'none',
        animation: false,
        label: { show: false },
        data: markLines,
      },
    } : {}),
  }));
  const formatter = (raw: number | string | Date): string =>
    new Date(Number(raw)).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  return {
    animation: false,
    backgroundColor: 'transparent',
    grid,
    legend: {
      top: 2, left: 6, right: 6, type: 'scroll',
      textStyle: { color: col.axis, fontSize: 10 }, itemHeight: 8, itemWidth: 13,
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: '#1b2534',
      borderColor: '#566274',
      textStyle: { color: '#f5f6fa', fontSize: 11 },
      axisPointer: { type: 'cross' },
      valueFormatter: val => val == null ? '—' : Number(val).toLocaleString('de-DE', { maximumFractionDigits: 1 }),
    },
    xAxis: {
      type: 'time', min: Date.parse(series.start), max: Date.parse(series.end),
      axisLabel: { color: col.axis, fontSize: 10, formatter },
      axisLine: { lineStyle: { color: col.gridLine } },
      splitLine: { show: false },
    },
    yAxis,
    dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'none', throttle: 100 }],
    series: charts,
  };
}

export function DataLoggerChart({
  kind, data, selectedEventId,
}: {
  kind: ChartKind;
  data: DataLoggerSeries;
  selectedEventId: number | null;
}) {
  const holder = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!holder.current) return;
    const graph = echarts.init(holder.current, undefined, { renderer: 'canvas' });
    const observer = new ResizeObserver(() => graph.resize());
    observer.observe(holder.current);
    return () => { observer.disconnect(); graph.dispose(); };
  }, []);

  useEffect(() => {
    if (!holder.current) return;
    echarts.getInstanceByDom(holder.current)?.setOption(makeOptions(kind, data, selectedEventId), { notMerge: true });
  }, [kind, data, selectedEventId]);

  const descriptions: Record<ChartKind, string> = {
    energy: 'Historischer Energiefluss mit Netz-, Miner- und Batterie-Leistung und Reglerereignissen',
    battery: 'Batterie-Ladeleistung, Entladeleistung und SOC mit Ereignismarkern',
    mining: 'Historische Minerleistung, Hashrate und Temperatur mit Ereignismarkern',
  };
  return <div ref={holder} role="img" aria-label={descriptions[kind]} style={{ width: '100%', height: 292 }} />;
}
