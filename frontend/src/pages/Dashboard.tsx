import { useEffect, useState } from 'react';
import { Alert, Badge, Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconAlertCircle, IconChartLine } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { getStatus } from '../api';
import type { Point } from '../components/TimeSeriesChart';
import { TimeSeriesChart } from '../components/TimeSeriesChart';

function formatPower(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${Math.round(value).toLocaleString('de-DE')} W`
    : '—';
}
function formatSOC(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toLocaleString('de-DE', { maximumFractionDigits: 1 })} %`
    : '—';
}

/** Reusable native Mantine Card for concise metrics (no custom card CSS). */
export function MetricCard({ title, value, detail }: { title: string; value: string; detail: string }) {
  return (
    <Card>
      <Stack gap={3}>
        <Text size="xs" fw={600} c="dimmed">{title}</Text>
        <Text size="xl" fw={700} lh={1.2} style={{ fontVariantNumeric: 'tabular-nums' }}>
          {value}
        </Text>
        <Text size="xs" c="dimmed" lineClamp={1} title={detail}>{detail}</Text>
      </Stack>
    </Card>
  );
}

export function Dashboard() {
  const { data, dataUpdatedAt, error, isPending } = useQuery({
    queryKey: ['status'],
    queryFn: getStatus,
    refetchInterval: 15000,
    refetchIntervalInBackground: false,
  });
  const [points, setPoints] = useState<Point[]>([]);
  const grid = data?.snapshot?.grid_power_w;

  useEffect(() => {
    if (!dataUpdatedAt || typeof grid !== 'number' || !Number.isFinite(grid)) return;
    setPoints((prev) => [...prev.slice(-39), { ts: dataUpdatedAt, value: grid }]);
  }, [dataUpdatedAt, grid]);

  const miners = data?.miners ?? [];
  const minerPower = miners.reduce((sum, m) => sum + (typeof m.power_w === 'number' ? m.power_w : 0), 0);
  const hashrateThs = miners.reduce((sum, m) => sum + (typeof m.current_hashrate_ghs === 'number' ? m.current_hashrate_ghs : 0), 0) / 1000;

  return (
    <Stack gap="sm">
      <Group justify="space-between" align="center" gap="xs">
        <div>
          <Title order={2} size="h3">Dashboard</Title>
          <Text c="dimmed" size="xs">Live-Status der Energie- und Miner-Steuerung</Text>
        </div>
        <Badge variant="light" color="gray" size="sm">GUI 2.0 · Vorschau</Badge>
      </Group>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="Backend nicht erreichbar">
          {String(error.message)}
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="xs">
        <MetricCard title="Netzanschluss" value={isPending ? 'Lade …' : formatPower(grid)} detail="Bezug (+) / Einspeisung (−)" />
        <MetricCard title="Batterie-SOC" value={isPending ? 'Lade …' : formatSOC(data?.snapshot?.battery_soc_pct)} detail="Ladezustand" />
        <MetricCard title="Miner-Leistung" value={isPending ? 'Lade …' : formatPower(minerPower)} detail={`${miners.length} Miner konfiguriert`} />
        <MetricCard title="Hashrate" value={isPending ? 'Lade …' : `${hashrateThs.toLocaleString('de-DE', { maximumFractionDigits: 2 })} TH/s`} detail="Gemeldete Gesamtleistung" />
      </SimpleGrid>

      <Card>
        <Group justify="space-between" gap="sm" wrap="nowrap">
          <Text fw={600} size="sm">Regler-Status</Text>
          <Text c="dimmed" size="xs" ta="right" lineClamp={2}>
            {data?.last_decision || 'Noch keine Reglerentscheidung verfügbar.'}
          </Text>
        </Group>
      </Card>

      <Card>
        <Group justify="space-between" align="center" mb={2} gap="xs">
          <Group gap={7}>
            <IconChartLine size={17} />
            <Text fw={600} size="sm">Netzleistung · Live-Trend</Text>
          </Group>
          <Badge color="gray" variant="outline" size="xs">Prototyp</Badge>
        </Group>
        <Text c="dimmed" size="xs" mb="xs">
          Temporärer 15-Sekunden-Trend · DataLogger-Historie folgt.
        </Text>
        {points.length < 2 ? (
          <Text c="dimmed" size="sm" ta="center" py={40}>
            Trend startet nach zwei Aktualisierungen.
          </Text>
        ) : (
          <TimeSeriesChart points={points} />
        )}
      </Card>

      <Text c="dimmed" size="xs">
        Entwicklungsansicht: nur lesend, keine Steuerbefehle.
      </Text>
    </Stack>
  );
}
