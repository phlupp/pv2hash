import { forwardRef, useMemo } from 'react';
import type { ButtonHTMLAttributes } from 'react';
import { Badge, Card, Group, Menu, ScrollArea, SimpleGrid, Stack, Text, Tooltip } from '@mantine/core';
import type { ControllerMarker, DataLoggerSeries } from '../types';
import { clusterEvents } from '../cluster';
import { formatDateTime, formatTime, formatUnit } from '../time';
import './timeline.css';

type EventButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  cluster: ReturnType<typeof clusterEvents>[number];
  selectedId: number | null;
};
const EventButton = forwardRef<HTMLButtonElement, EventButtonProps>(function EventButton({
  cluster, selectedId, ...rest
}, ref) {
  const active = cluster.events.some(event => event.event_id === selectedId);
  const direction = cluster.latest.direction === 'up' ? 'up' : cluster.latest.direction === 'down' ? 'down' : 'neutral';
  return (
    <button
      {...rest}
      type="button"
      ref={ref}
      className={`pv-event-point pv-event-${direction}${active ? ' pv-event-active' : ''} ${rest.className || ''}`}
      style={{ ...rest.style, left: `${cluster.percentage}%` }}
      aria-pressed={active}
      aria-label={`${cluster.events.length} Reglerentscheidung${cluster.events.length === 1 ? '' : 'en'} um ${formatDateTime(cluster.latest.ts || cluster.latest.at)}`}
    >
      {cluster.events.length > 1 ? <span className="pv-event-count">{cluster.events.length}</span> : (direction === 'up' ? '↑' : direction === 'down' ? '↓' : '•')}
    </button>
  );
});

export function ControllerTimeline({
  data, selectedId, onSelect,
}: {
  data: DataLoggerSeries;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const clusters = useMemo(() => clusterEvents(data.markers, data.start, data.end), [data.markers, data.start, data.end]);
  const selected = data.markers.find(m => m.event_id === selectedId) || data.markers.at(-1);
  return (
    <Card>
      <Stack gap="xs">
        <Group justify="space-between" align="center" gap="xs">
          <div>
            <Text fw={600}>Reglerentscheidungen</Text>
            <Text c="dimmed" size="xs">Tatsächlich angewendete Profilwechsel · Ereignis auswählen für Details und Chart-Markierung</Text>
          </div>
          <Badge size="sm" variant="outline" color="gray">
            {data.marker_count.toLocaleString('de-DE')} Events
          </Badge>
        </Group>
        {data.markers.length >= 500 && (
          <Text size="xs" c="dimmed">
            Hinweis: Core 0.9.4 liefert maximal die ersten 500 Reglerereignisse dieses Zeitfensters.
            Neuere Entscheidungen können in dieser Ansicht fehlen.
          </Text>
        )}
        {clusters.length ? (
          <ScrollArea type="auto" scrollbars="x" offsetScrollbars="x" aria-label="Zeitstrahl der Reglerentscheidungen">
            <div className="pv-event-timeline">
              <div className="pv-event-line" />
              {clusters.map(cluster => {
                if (cluster.events.length === 1) {
                  const event = cluster.latest;
                  return (
                    <Tooltip key={cluster.index} label={`${formatDateTime(event.ts || event.at)} · ${event.label || event.miner_name || 'Miner'}`} withArrow>
                      <EventButton cluster={cluster} selectedId={selected?.event_id ?? null} onClick={() => onSelect(event.event_id)} />
                    </Tooltip>
                  );
                }
                return (
                  <Menu key={cluster.index} position="bottom" width={290} withinPortal shadow="sm">
                    <Menu.Target><EventButton cluster={cluster} selectedId={selected?.event_id ?? null} /></Menu.Target>
                    <Menu.Dropdown>
                      <Menu.Label>{cluster.events.length} Reglerentscheidungen · einzelne Ereignisse wählen</Menu.Label>
                      <ScrollArea.Autosize mah={240}>
                        {cluster.events.slice().reverse().map((event: ControllerMarker) => (
                          <Menu.Item key={event.event_id} onClick={() => onSelect(event.event_id)}>
                            <Text size="xs" fw={600}>{formatDateTime(event.ts || event.at)} · {event.miner_name || 'Miner'}</Text>
                            <Text size="xs" c="dimmed">{event.old_profile || '?'} → {event.new_profile || '?'} · {event.reason_text || event.reason_code || 'Profilwechsel'}</Text>
                          </Menu.Item>
                        ))}
                      </ScrollArea.Autosize>
                    </Menu.Dropdown>
                  </Menu>
                );
              })}
              <span className="pv-event-start">{formatTime(data.start, data.range)}</span>
              <span className="pv-event-end">{formatTime(data.end, data.range)}</span>
            </div>
          </ScrollArea>
        ) : (
          <Text c="dimmed" size="sm" py="sm">Keine ausgeführten Reglerentscheidungen im ausgewählten Zeitraum gespeichert.</Text>
        )}
        {selected && (
          <Stack gap="xs" p="sm" bg="dark.8" style={{ borderRadius: 'var(--mantine-radius-sm)' }}>
            <Group justify="space-between" gap="xs">
              <Text fw={600} size="sm">{selected.miner_name || 'Miner'} · {selected.old_profile || '?'} → {selected.new_profile || '?'}</Text>
              <Text size="xs" c="dimmed">{formatDateTime(selected.ts || selected.at)}</Text>
            </Group>
            <Text size="sm" c="dimmed">{selected.reason_text || selected.reason_code || 'Profilwechsel'}</Text>
            <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="xs">
              <div><Text size="xs" c="dimmed">Netz</Text><Text size="sm">{formatUnit(selected.grid_power_w, ' W')}</Text></div>
              <div><Text size="xs" c="dimmed">Batterie-SOC</Text><Text size="sm">{formatUnit(selected.battery_soc_pct, ' %')}</Text></div>
              <div><Text size="xs" c="dimmed">Batteriezustand</Text><Text size="sm">{selected.battery_direction || '—'}</Text></div>
            </SimpleGrid>
            {!!selected.flags?.length && (
              <Group gap={5}>
                {selected.flags.slice(0, 7).map(flag => <Badge key={flag} size="xs" variant="outline" color="gray" radius="sm">{flag}</Badge>)}
              </Group>
            )}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}
