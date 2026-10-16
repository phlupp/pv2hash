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

function MetricCard({ title, value, detail }: { title: string; value: string; detail: string }) {
  return (
    <Card className="pv-card" padding="lg">
      <Text size="sm" c="dimmed">{title}</Text>
      <Title order={2} mt={6}>{value}</Title>
      <Text size="xs" c="dimmed" mt={6}>{detail}</Text>
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
    <Stack gap="lg">
      <div>
        <Group justify="space-between" align="center">
          <Title order={1}>Dashboard</Title>
          <Badge variant="light" color="violet">Neue Oberfläche · Vorschau</Badge>
        </Group>
        <Text c="dimmed" size="sm" mt={6}>
          Erste React-Komponenten mit lesender Anbindung an die vorhandene PV2Hash-API.
        </Text>
      </div>
      {error && <Alert color="red" icon={<IconAlertCircle size={18} />} title="Backend nicht erreichbar">{String(error.message)}</Alert>}
      <SimpleGrid cols={{ base: 1, xs: 2, lg: 4 }} spacing="md">
        <MetricCard title="Netzanschluss" value={isPending ? 'Lade …' : formatPower(grid)} detail="Positiv = Bezug, negativ = Einspeisung" />
        <MetricCard title="Batterie-SOC" value={isPending ? 'Lade …' : formatSOC(data?.snapshot?.battery_soc_pct)} detail="Sofern Batteriesensor verfügbar" />
        <MetricCard title="Miner-Leistung" value={isPending ? 'Lade …' : formatPower(minerPower)} detail={`${miners.length} konfigurierte Miner`} />
        <MetricCard title="Hashrate" value={isPending ? 'Lade …' : `${hashrateThs.toLocaleString('de-DE', { maximumFractionDigits: 2 })} TH/s`} detail="Summe der gemeldeten Miner-Werte" />
      </SimpleGrid>
      <Card className="pv-card" padding="lg">
        <Group justify="space-between" mb="xs">
          <Group gap="xs"><IconChartLine size={19}/><Title order={3}>Netzleistung · Live-Trend</Title></Group>
          <Badge color="gray" variant="outline">Diagramm-Prototyp</Badge>
        </Group>
        <Text c="dimmed" size="sm" mb="md">
          Erste ECharts-Komponente. Werte werden nur während der geöffneten Dashboard-Seite gesammelt; die echte DataLogger-Historie folgt bei der Migration.
        </Text>
        {points.length < 2 ? (
          <Text c="dimmed" ta="center" py={50}>Trend erscheint nach zwei erfolgreichen Aktualisierungen (15-Sekunden-Intervall).</Text>
        ) : (
          <TimeSeriesChart points={points} />
        )}
      </Card>
      <Card className="pv-card" padding="lg">
        <Title order={3}>Regelung</Title>
        <Text c="dimmed" size="sm" mt="sm">{data?.last_decision || 'Noch keine Reglerentscheidung verfügbar.'}</Text>
        <Text c="dimmed" size="xs" mt="md">Keine Steuerbefehle: Das neue Frontend ist in dieser Phase ausschließlich lesend.</Text>
      </Card>
    </Stack>
  );
}
