import { Anchor, Badge, Card, Group, ScrollArea, SimpleGrid, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconCpu, IconExternalLink, IconPlayerPauseFilled, IconPlayerPlayFilled, IconWifi, IconWifiOff } from '@tabler/icons-react';
import { Metric } from '../components/Metric';
import { minerWebGuiUrl } from '../minerLink';
import type { DashboardSectionDefinition, DashboardContext } from '../types';

function MiningSection({ context }: { context: DashboardContext }) {
  const data = context.live;
  const telemetryMiners = context.telemetry?.miners ?? [];
  const reported = telemetryMiners.filter(m => typeof m.current_hashrate_ghs === 'number' && Number.isFinite(m.current_hashrate_ghs));
  const totalThs = reported.reduce((sum, m) => sum + (m.current_hashrate_ghs ?? 0), 0) / 1000;
  const hashrateText = reported.length > 0 ? `${totalThs.toLocaleString('de-DE', { maximumFractionDigits: 2 })} TH/s` : '—';
  return (
    <Stack gap="xs">
      <Group gap={7}><IconCpu size={18} /><Text fw={650}>Mining</Text></Group>
      <SimpleGrid cols={{ base: 2, md: 2 }} spacing="xs">
        <Metric label="Miner-Leistung" value={data?.cards.miners?.power ?? '—'} detail={data?.cards.miners?.meta ?? 'Miner werden geladen'} />
        <Metric label="Gesamt-Hashrate" value={hashrateText} detail={reported.length ? `${reported.length} Miner mit Hashrate-Wert` : 'Keine Hashrate-Messwerte vorhanden'} />
      </SimpleGrid>
      <Card padding={0}>
        <ScrollArea type="auto" scrollbars="x" offsetScrollbars="x">
          <Table striped highlightOnHover horizontalSpacing={6} verticalSpacing={7} miw={420}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Miner</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th w={50} style={{ whiteSpace: 'nowrap' }}>Profil</Table.Th>
                <Table.Th ta="right">Leistung</Table.Th>
                <Table.Th ta="right">Hashrate</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(data?.miners ?? []).map(miner => {
                const webGuiUrl = minerWebGuiUrl(miner.host_gui_url);
                return (
                <Table.Tr key={miner.id}>
                  <Table.Td>
                    {webGuiUrl ? (
                      <Tooltip label={`Miner-Weboberfläche öffnen: ${miner.host}`} withArrow>
                        <Anchor
                          href={webGuiUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          size="sm"
                          fw={600}
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}
                        >
                          {miner.name}<IconExternalLink size={12} aria-label="Externer Link" />
                        </Anchor>
                      </Tooltip>
                    ) : (
                      <Text size="sm" fw={600}>{miner.name}</Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap={7} wrap="nowrap">
                      <Tooltip label={miner.control_enabled ? 'In Regelung' : 'Nicht in Regelung'}>
                        <Badge size="xs" color={miner.control_enabled ? 'teal' : 'gray'} variant="light">R</Badge>
                      </Tooltip>
                      <Tooltip label={miner.reachable ? 'Erreichbar' : 'Nicht erreichbar'}>
                        <span style={{ display: 'inline-flex' }}>{miner.reachable ? <IconWifi size={17} color="var(--mantine-color-teal-5)" /> : <IconWifiOff size={17} color="var(--mantine-color-gray-5)" />}</span>
                      </Tooltip>
                      <Tooltip label={miner.is_running ? 'Läuft' : 'Gestoppt / pausiert'}>
                        <span style={{ display: 'inline-flex' }}>{miner.is_running ? <IconPlayerPlayFilled size={15} color="var(--mantine-color-teal-5)" /> : <IconPlayerPauseFilled size={15} color="var(--mantine-color-gray-5)" />}</span>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                  <Table.Td w={50} style={{ whiteSpace: 'nowrap' }}><Badge size="xs" variant="outline" color="gray">{miner.profile || 'off'}</Badge></Table.Td>
                  <Table.Td ta="right"><Text size="sm">{miner.power_text || '—'}</Text></Table.Td>
                  <Table.Td ta="right"><Text size="sm">{miner.hashrate_text || '—'}</Text></Table.Td>
                </Table.Tr>
                );
              })}
              {!context.loading && !data?.miners?.length && (
                <Table.Tr><Table.Td colSpan={5}><Text size="sm" c="dimmed">Keine Miner konfiguriert.</Text></Table.Td></Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>
    </Stack>
  );
}

export const dashboardSection: DashboardSectionDefinition = {
  id: 'mining',
  order: 30,
  Component: MiningSection,
};
