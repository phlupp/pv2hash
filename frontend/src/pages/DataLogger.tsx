import { useState } from 'react';
import {
  Alert, Badge, Button, Card, Collapse, Group, LoadingOverlay, MultiSelect,
  ScrollArea, SegmentedControl, SimpleGrid, Stack, Table, Text, Title,
} from '@mantine/core';
import { IconAlertCircle, IconArrowLeft, IconArrowRight, IconBug, IconPlayerPlay, IconRefresh } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { getStatus } from '../api';
import { fetchLoggerDebug, fetchLoggerSeries, fetchLoggerStatus } from '../datalogger/api';
import { ControllerTimeline } from '../datalogger/components/ControllerTimeline';
import { DataLoggerChart } from '../datalogger/charts/DataLoggerChart';
import { RANGES, formatDateTime, formatTime, formatUnit, nextWindowEnd, previousWindowEnd } from '../datalogger/time';
import type { DataLoggerRange } from '../datalogger/types';

const refreshMs = 30_000;

export function DataLogger() {
  const [range, setRange] = useState<DataLoggerRange>('1h');
  const [end, setEnd] = useState<string | null>(null);
  const [selectedMinerIds, setSelectedMinerIds] = useState<string[]>([]);
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const live = end === null;

  const params = { range, end, minerIds: selectedMinerIds };
  const status = useQuery({
    queryKey: ['datalogger', 'status'],
    queryFn: fetchLoggerStatus,
    refetchInterval: refreshMs,
  });
  const series = useQuery({
    queryKey: ['datalogger', 'series', range, end, selectedMinerIds],
    queryFn: () => fetchLoggerSeries(params),
    refetchInterval: live ? refreshMs : false,
    refetchIntervalInBackground: false,
  });
  const debug = useQuery({
    queryKey: ['datalogger', 'debug', range, end, selectedMinerIds],
    queryFn: () => fetchLoggerDebug(params),
    enabled: debugOpen,
    refetchInterval: live && debugOpen ? refreshMs : false,
    refetchIntervalInBackground: false,
  });
  const telemetry = useQuery({ queryKey: ['status'], queryFn: getStatus, staleTime: 10_000 });
  const data = series.data;
  const selectedMarker = data?.markers.find(m => m.event_id === selectedEventId) || data?.markers.at(-1);
  const hasBatterySamples = data?.points.some(p =>
    p.battery_soc_pct != null || p.battery_charge_power_w != null || p.battery_discharge_power_w != null,
  );
  const batteryEnabled = telemetry.data?.config?.battery?.enabled === true;
  const displayBattery = hasBatterySamples || batteryEnabled;
  const minerOptions = data?.miners.map(m => ({ value: m.id, label: m.name || m.key || m.id })) || [];

  function switchRange(next: string) {
    if (!RANGES.includes(next as DataLoggerRange)) return;
    setRange(next as DataLoggerRange);
    setSelectedEventId(null);
  }
  function goPrevious() {
    const reference = data?.end || end || new Date().toISOString();
    setEnd(previousWindowEnd(reference, range));
    setSelectedEventId(null);
  }
  function goNext() {
    if (live) return;
    setEnd(nextWindowEnd(end, range));
    setSelectedEventId(null);
  }
  function goLive() {
    setEnd(null);
    setSelectedEventId(null);
  }

  const windowLabel = data && !live
    ? `${formatTime(data.start, range)} – ${formatTime(data.end, range)}`
    : 'Aktuelles Zeitfenster';

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start" gap="xs">
        <div>
          <Title order={2} size="h3">Data Logger</Title>
          <Text c="dimmed" size="xs">Zeitreihen und tatsächlich ausgeführte Reglerentscheidungen</Text>
        </div>
        <Group gap={6}>
          <Badge variant="light" color={status.data?.enabled ? 'teal' : 'gray'} size="sm">
            {status.data?.enabled ? 'Aktiv' : 'Inaktiv'}
          </Badge>
          <Badge variant="outline" color="gray" size="sm">{status.data?.interval_seconds ?? '—'} s</Badge>
          <Badge variant="outline" color="gray" size="sm">{status.data?.retention_days ?? '—'} Tage</Badge>
        </Group>
      </Group>

      {(status.error || series.error) && (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="DataLogger nicht erreichbar">
          {status.error?.message || series.error?.message}
        </Alert>
      )}
      {!!status.data?.last_error && <Alert color="red" title="Aufzeichnungsfehler">{status.data.last_error}</Alert>}

      <Card>
        <Stack gap="xs">
          <Group justify="space-between" align="center" gap="sm">
            <SegmentedControl
              aria-label="Zeitspanne des DataLoggers"
              value={range}
              onChange={switchRange}
              data={RANGES.map(r => ({ label: r, value: r }))}
              size="xs"
              color="violet"
            />
            <Group gap={6} wrap="nowrap">
              <Button size="compact-sm" variant="light" color="gray" leftSection={<IconArrowLeft size={15} />} onClick={goPrevious}>Zurück</Button>
              <Button size="compact-sm" variant="light" color="gray" leftSection={<IconArrowRight size={15} />} onClick={goNext} disabled={live}>Weiter</Button>
              <Button
                size="compact-sm"
                variant={live ? 'filled' : 'light'}
                color={live ? 'violet' : 'gray'}
                leftSection={<IconPlayerPlay size={13} />}
                onClick={goLive}
                aria-pressed={live}
              >Live</Button>
            </Group>
          </Group>
          <Group justify="space-between" gap="xs">
            <Group gap={6}>
              <Badge size="xs" variant="light" color={live ? 'teal' : 'gray'}>{live ? 'LIVE' : 'HISTORIE'}</Badge>
              <Text c="dimmed" size="xs">{windowLabel}</Text>
            </Group>
            <Text c="dimmed" size="xs">
              {series.isFetching ? 'Aktualisiere …' : data ? `${data.point_count} Punkte · ${data.raw_count.toLocaleString('de-DE')} Rohdaten` : 'Lade …'}
            </Text>
          </Group>
          <MultiSelect
            label="Miner"
            description="Standard: alle Miner; die Auswahl filtert Mining-Diagramme und Reglerereignisse."
            placeholder="Alle Miner"
            data={minerOptions}
            value={selectedMinerIds}
            onChange={ids => { setSelectedMinerIds(ids); setSelectedEventId(null); }}
            searchable
            clearable
            size="xs"
            comboboxProps={{ withinPortal: true }}
            styles={{ input: { minHeight: 32 } }}
          />
        </Stack>
      </Card>

      <div style={{ position: 'relative', minHeight: 110 }}>
        <LoadingOverlay visible={series.isPending} loaderProps={{ size: 'sm' }} overlayProps={{ blur: 0 }} />
        {data && (
          <Stack gap="md">
            <ControllerTimeline
              data={data}
              selectedId={selectedMarker?.event_id ?? null}
              onSelect={setSelectedEventId}
            />

            {data.points.length ? (
              <>
                <Card>
                  <Group justify="space-between" mb="xs" gap="xs">
                    <Text fw={600}>Energiefluss</Text>
                    <Badge variant="outline" color="gray" size="xs">Watt</Badge>
                  </Group>
                  <DataLoggerChart kind="energy" data={data} selectedEventId={selectedMarker?.event_id ?? null} />
                </Card>
                {displayBattery && (
                  <Card>
                    <Group justify="space-between" mb="xs" gap="xs">
                      <Text fw={600}>Batterie</Text>
                      <Badge variant="outline" color="gray" size="xs">W · SOC %</Badge>
                    </Group>
                    {!hasBatterySamples && <Text c="dimmed" size="xs">Im ausgewählten Zeitraum liegen noch keine Batteriesamples vor.</Text>}
                    <DataLoggerChart kind="battery" data={data} selectedEventId={selectedMarker?.event_id ?? null} />
                  </Card>
                )}
                <Card>
                  <Group justify="space-between" mb="xs" gap="xs">
                    <Text fw={600}>Mining</Text>
                    <Badge variant="outline" color="gray" size="xs">W · GH/s · °C</Badge>
                  </Group>
                  <DataLoggerChart kind="mining" data={data} selectedEventId={selectedMarker?.event_id ?? null} />
                </Card>
              </>
            ) : (
              <Card><Text size="sm" c="dimmed">Für dieses Zeitfenster sind keine Messwerte aufgezeichnet.</Text></Card>
            )}
          </Stack>
        )}
      </div>

      <Card>
        <Group justify="space-between" align="center" gap="xs" mb={debugOpen ? 'sm' : 0}>
          <Group gap={6}>
            <IconBug size={18} />
            <div>
              <Text fw={600} size="sm">Regler-Debug</Text>
              <Text c="dimmed" size="xs">Wünsche, blockierte Umschaltungen und Hold-Entscheidungen · lokale Kurzzeithistorie</Text>
            </div>
          </Group>
          <Button
            variant="light"
            color="gray"
            size="compact-sm"
            onClick={() => setDebugOpen(open => !open)}
            aria-expanded={debugOpen}
          >
            {debugOpen ? 'Ausblenden' : 'Anzeigen'}
          </Button>
        </Group>
        <Collapse expanded={debugOpen}>
          <Stack gap="xs">
            {debug.error && <Alert color="red">{debug.error.message}</Alert>}
            <Group gap="xs">
              <Badge size="xs" color="gray" variant="outline">{debug.data?.event_count ?? '—'} Debug-Events</Badge>
              <Badge size="xs" color="gray" variant="outline">{debug.data?.retention_hours ?? 48} h Aufbewahrung</Badge>
              {debug.isFetching && <IconRefresh size={14} />}
            </Group>
            <ScrollArea h={350} type="auto" scrollbars="xy" offsetScrollbars="x">
              <Table striped highlightOnHover withTableBorder={false} horizontalSpacing="xs" verticalSpacing={7} miw={800}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Zeit</Table.Th><Table.Th>Typ</Table.Th><Table.Th>Miner</Table.Th>
                    <Table.Th>Profil</Table.Th><Table.Th>Grund</Table.Th>
                    <Table.Th ta="right">Netz</Table.Th><Table.Th ta="right">SOC</Table.Th><Table.Th>Flags</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {debug.data?.events.map(event => (
                    <Table.Tr key={event.event_id}>
                      <Table.Td><Text size="xs" style={{ whiteSpace: 'nowrap' }}>{formatDateTime(event.at)}</Text></Table.Td>
                      <Table.Td><Badge color="gray" size="xs" variant="light">{event.event_type}</Badge></Table.Td>
                      <Table.Td><Text size="xs">{event.miner_name || 'Alle'}</Text></Table.Td>
                      <Table.Td><Text size="xs" style={{ whiteSpace: 'nowrap' }}>{event.current_profile || '?'} → {event.requested_profile || event.effective_profile || '?'}</Text></Table.Td>
                      <Table.Td><Text size="xs" lineClamp={2}>{event.reason_text || event.reason_code || '—'}</Text></Table.Td>
                      <Table.Td ta="right"><Text size="xs">{formatUnit(event.grid_power_w, ' W')}</Text></Table.Td>
                      <Table.Td ta="right"><Text size="xs">{formatUnit(event.battery_soc_pct, ' %')}</Text></Table.Td>
                      <Table.Td><Text size="xs" lineClamp={2}>{event.flags?.join(', ') || '—'}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                  {debug.data && !debug.data.events.length && (
                    <Table.Tr><Table.Td colSpan={8}><Text c="dimmed" size="sm">In diesem Zeitraum keine Debug-Ereignisse.</Text></Table.Td></Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Stack>
        </Collapse>
      </Card>
      <Text size="xs" c="dimmed">GUI 2.0 · Echtzeitabfrage alle 30 Sekunden im Live-Modus; historische Fenster werden nicht automatisch aktualisiert.</Text>
    </Stack>
  );
}
