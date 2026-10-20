import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { wattsToKw, formatKw, formatKwTick } from './units';
import type { EnergySummary } from './energy';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export type ForecastPoint = { ts: string; power_w: number; issued_at?: string };
export type ForecastDay = {
  day: string; today: string; timezone: string; start: string; end: string;
  forecast: ForecastPoint[]; actual: ForecastPoint[]; status: string;
  shadow?: ForecastPoint[];
  shadow_status?: {
    status: 'not_available' | 'learning' | 'unstable' | 'calibrated';
    factor: number; sample_count: number; day_count: number;
    model_version: string; configuration_hint: string | null;
  };
  energy: { forecast: EnergySummary; actual: EnergySummary };
};

export function ForecastChart({ data, showShadow = true }: {
  data: ForecastDay; showShadow?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const plot = echarts.init(ref.current);
    const observer = new ResizeObserver(() => plot.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); plot.dispose(); };
  }, []);

  useEffect(() => {
    if (!ref.current) return;
    const graph = echarts.getInstanceByDom(ref.current);
    const line = (name: string, points: ForecastPoint[], color: string, dashed = false) => ({
      name, type: 'line' as const,
      data: points.map(p => [Date.parse(p.ts), wattsToKw(p.power_w)]),
      showSymbol: false, connectNulls: false, smooth: false,
      animation: false,
      lineStyle: { color, width: dashed ? 2 : 2.6, type: dashed ? 'dashed' as const : 'solid' as const },
      itemStyle: { color },
    });
    const option: EChartsOption = {
      animation: false,
      backgroundColor: 'transparent',
      grid: { left: 52, right: 20, top: 48, bottom: 42 },
      legend: { top: 0, textStyle: { color: '#aab0b9' } },
      tooltip: {
        trigger: 'axis', confine: true,
        backgroundColor: '#1b2534', borderColor: '#596576',
        textStyle: { color: '#f5f6fa' },
        valueFormatter: value => value == null ? '—' : formatKw(Number(value)),
      },
      xAxis: {
        type: 'time', min: Date.parse(data.start), max: Date.parse(data.end),
        splitLine: { show: false },
        axisLabel: {
          color: '#aab0b9',
          formatter: x => new Date(Number(x)).toLocaleTimeString('de-DE', {
            hour: '2-digit', minute: '2-digit', timeZone: data.timezone,
          }),
        },
      },
      yAxis: {
        type: 'value', name: 'kW', min: 0,
        axisLabel: {
          color: '#aab0b9',
          formatter: formatKwTick,
        },
        splitLine: { lineStyle: { color: 'rgba(170,176,185,.13)' } },
      },
      series: [
        line('Prognose', data.forecast, '#a3a8b2', true),
        line('Ist-Erzeugung', data.actual, '#E8C047'),
        ...(showShadow && (data.shadow?.length ?? 0) > 0
          ? [line('PV2Hash Shadow', data.shadow ?? [], '#a855f7', true)] : []),
      ],
    };
    graph?.setOption(option, { notMerge: true });
  }, [data, showShadow]);

  return <div ref={ref} role="img"
    aria-label="PV-Prognose grau gestrichelt, Ist-Erzeugung gelb, PV2Hash Shadow optional lila gestrichelt; ganzer Kalendertag"
    style={{ width: '100%', height: 340, minHeight: 270 }} />;
}
