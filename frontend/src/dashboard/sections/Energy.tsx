import { Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconBolt } from '@tabler/icons-react';
import { Metric } from '../components/Metric';
import { QualityBadge } from '../components/QualityBadge';
import type { DashboardSectionDefinition, DashboardContext } from '../types';
import { energyCardLayout } from '../energyCardLayout';

function formatPower(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${Math.round(value).toLocaleString('de-DE')} W`
    : '—';
}

function EnergySection({ context }: { context: DashboardContext }) {
  const cards = context.live?.cards;
  const snap = context.telemetry?.snapshot;
  const config = context.telemetry?.config;
  const gridValid = typeof snap?.grid_power_w === 'number' && Number.isFinite(snap.grid_power_w);
  // PV comes from its own non-regulating background poller, not the controller snapshot.
  const pvData = cards?.pv;
  const pvValid = pvData?.quality === 'live'
    && typeof pvData.power_w === 'number' && Number.isFinite(pvData.power_w);
  const batteryEnabled = config?.battery?.enabled === true || snap?.battery_soc_pct != null || snap?.battery_is_active === true;
  const { showPv, columns } = energyCardLayout(pvData?.enabled, batteryEnabled);
  const batteryValue = cards?.battery?.value ?? '—';
  const charging = snap?.battery_is_charging && typeof snap.battery_charge_power_w === 'number';
  const discharging = snap?.battery_is_discharging && typeof snap.battery_discharge_power_w === 'number';
  const batteryHasValue = snap?.battery_soc_pct != null || snap?.battery_charge_power_w != null || snap?.battery_discharge_power_w != null;
  const batteryDetail = charging
    ? `Lädt mit ${formatPower(snap?.battery_charge_power_w)}`
    : discharging
      ? `Entlädt mit ${formatPower(snap?.battery_discharge_power_w)}`
      : cards?.battery?.state ?? 'Batteriestatus nicht verfügbar';

  return (
    <Stack gap="xs">
      <Group gap={7}><IconBolt size={18} /><Text fw={650}>Energie</Text></Group>
      <SimpleGrid cols={{ base: Math.min(2, columns), md: columns }} spacing="xs">
        <Metric
          label="Netzleistung"
          value={cards?.grid?.value ?? '—'}
          detail="Bezug (+) / Einspeisung (−)"
          accent={cards?.grid?.class === 'good' ? 'teal' : cards?.grid?.class === 'warn' ? 'orange' : undefined}
          quality={<QualityBadge quality={snap?.quality} measured={gridValid} source={cards?.source?.label ?? config?.source?.name} />}
        />
        {showPv && <Metric
          label="PV-Erzeugung"
          value={pvValid ? formatPower(pvData?.power_w) : '—'}
          detail={pvData?.quality === 'offline' ? 'Modbus-Messung nicht erreichbar'
            : pvValid ? 'Aktuelle PV-Leistung aus Modbus TCP' : 'Warte auf PV-Messwert'}
          quality={
            <QualityBadge
              quality={pvData?.quality}
              measured={pvValid}
              source={pvData?.source ?? config?.pv?.name}
            />
          }
        />}
        {batteryEnabled && (
          <Metric
            label="Batterie"
            value={batteryValue}
            detail={batteryDetail}
            accent={cards?.battery?.class === 'good' ? 'teal' : cards?.battery?.class === 'warn' ? 'orange' : undefined}
            quality={
              <QualityBadge
                quality={snap?.battery_quality}
                measured={batteryHasValue}
                source={config?.battery?.name}
              />
            }
          />
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
