import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

export interface Point {
  ts: number;
  value: number;
}

export function TimeSeriesChart({ points }: { points: Point[] }) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.init(container.current, undefined, { renderer: 'canvas' });
    const resizeObserver = new ResizeObserver(() => chart.resize());
    resizeObserver.observe(container.current);
    return () => {
      resizeObserver.disconnect();
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    if (!container.current) return;
    const chart = echarts.getInstanceByDom(container.current);
    if (!chart) return;
    const options: EChartsOption = {
      animation: false,
      grid: { left: 68, top: 12, right: 20, bottom: 44 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#25294e',
        borderColor: '#4c527a',
        textStyle: { color: '#f1f3ff' },
        valueFormatter: (value) => `${Number(value).toFixed(0)} W`,
      },
      xAxis: {
        type: 'time',
        axisLabel: { color: '#b6bcdf' },
        axisLine: { lineStyle: { color: '#525878' } },
      },
      yAxis: {
        type: 'value',
        name: 'W',
        nameTextStyle: { color: '#b6bcdf' },
        axisLabel: { color: '#b6bcdf' },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,.09)' } },
      },
      dataZoom: [{ type: 'inside', xAxisIndex: 0 }],
      series: [{
        type: 'line',
        name: 'Netzleistung',
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: { color: '#a47bff', width: 2 },
        itemStyle: { color: '#a47bff' },
        data: points.map((point) => [point.ts, point.value]),
      }],
    };
    chart.setOption(options, { notMerge: true });
  }, [points]);

  return <div ref={container} role="img" aria-label="Live-Trend der Netzleistung" style={{ width: '100%', height: 260 }} />;
}
