import { Badge, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconBolt } from '@tabler/icons-react';
import { Metric } from '../components/Metric';
import type { DashboardSectionDefinition, DashboardContext } from '../types';

function formatPower(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${Math.round(value).toLocaleString('de-DE')} W`
    : '—';
}

function EnergySection({ context }: { context: DashboardContext }) {
  const cards = context.live?.cards;
  const snap = context.telemetry?.snapshot;
  const pvValid = typeof snap?.pv_power_w === 'number' && Number.isFinite(snap.pv_power_w);
  const batteryEnabled = context.telemetry?.config?.battery?.enabled === true || snap?.battery_soc_pct != null || snap?.battery_is_active === true;
  const batteryValue = cards?.battery?.value ?? '—';
  const charging = snap?.battery_is_charging && typeof snap.battery_charge_power_w === 'number';
  const discharging = snap?.battery_is_discharging && typeof snap.battery_discharge_power_w === 'number';
  const batteryDetail = charging
    ? `Lädt mit ${formatPower(snap?.battery_charge_power_w)}`
    : discharging
      ? `Entlädt mit ${formatPower(snap?.battery_discharge_power_w)}`
      : cards?.battery?.state ?? 'Batteriestatus nicht verfügbar';

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="center" gap="xs">
        <Group gap={7}><IconBolt size={18} /><Text fw={650}>Energie</Text></Group>
        <Group gap={5}>
          <Text c="dimmed" size="xs">{cards?.source?.label ?? 'Quelle unbekannt'}</Text>
          <Badge variant="light" color={snap?.quality === 'ok' ? 'teal' : 'gray'} size="xs">
            {cards?.source?.quality ?? 'Qualität: —'}
          </Badge>
        </Group>
      </Group>
      <SimpleGrid cols={{ base: 2, md: batteryEnabled ? 3 : 2 }} spacing="xs">
        <Metric
          label="Netzleistung"
          value={cards?.grid?.value ?? '—'}
          detail="Bezug (+) / Einspeisung (−)"
          accent={cards?.grid?.class === 'good' ? 'teal' : cards?.grid?.class === 'warn' ? 'orange' : undefined}
        />
        <Metric
          label="PV-Erzeugung"
          value={pvValid ? formatPower(snap?.pv_power_w) : '—'}
          detail={pvValid ? 'PV-Leistungswert aus der API' : 'Keine PV-Erzeugungsmessung vorhanden'}
        />
        {batteryEnabled && (
          <Metric label="Batterie" value={batteryValue} detail={batteryDetail} accent={cards?.battery?.class === 'good' ? 'teal' : cards?.battery?.class === 'warn' ? 'orange' : undefined} />
        )}
      </SimpleGrid>
    </Stack>
  );
}

export const dashboardSection: DashboardSectionDefinition = {
  id: 'energy',
  order: 10,
  Component: EnergySection,
};
