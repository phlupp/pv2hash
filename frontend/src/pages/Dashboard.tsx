import { Alert, Stack, Text, Title } from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { getDashboardStatus, getStatus } from '../api';
import { dashboardSections } from '../dashboard/registry';
import type { DashboardContext } from '../dashboard/types';

export function Dashboard() {
  const live = useQuery({
    queryKey: ['dashboard', 'status'],
    queryFn: getDashboardStatus,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });
  const telemetry = useQuery({
    queryKey: ['status'],
    queryFn: getStatus,
    refetchInterval: 15000,
    refetchIntervalInBackground: false,
  });

  const context: DashboardContext = {
    live: live.data,
    telemetry: telemetry.data,
    loading: live.isPending,
  };

  return (
    <Stack gap="md">
      <div>
        <Title order={2} size="h3">Dashboard</Title>
        <Text c="dimmed" size="xs">Live-Status der Energie- und Miner-Steuerung</Text>
      </div>
      {(live.error || telemetry.error) && (
        <Alert color="red" icon={<IconAlertCircle size={18} />} title="Verbindung gestört">
          {live.error?.message || telemetry.error?.message || 'Messwerte konnten nicht aktualisiert werden.'}
        </Alert>
      )}
      {dashboardSections.filter(section => section.enabled?.(context) ?? true).map(section => (
        <section key={section.id} aria-label={section.id}>
          <section.Component context={context} />
        </section>
      ))}
    </Stack>
  );
}
