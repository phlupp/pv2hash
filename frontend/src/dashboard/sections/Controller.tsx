import { Badge, Card, Group, Progress, Stack, Text } from '@mantine/core';
import { IconAdjustmentsHorizontal } from '@tabler/icons-react';
import type { DashboardSectionDefinition } from '../types';
import type { DashboardContext } from '../types';

function ControllerSection({ context }: { context: DashboardContext }) {
  const data = context.live?.cards.controller;
  const progress = typeof data?.ring_progress === 'number'
    ? Math.max(0, Math.min(100, data.ring_progress * 100))
    : 100;
  return (
    <Stack gap="xs">
      <Group gap={7}><IconAdjustmentsHorizontal size={18} /><Text fw={650}>Regelung</Text></Group>
      <Card>
        <Stack gap="xs">
          <Group justify="space-between" align="center" gap="xs">
            <Text fw={600} size="sm">{data?.summary || 'Reglerstatus wird geladen'}</Text>
            <Badge size="xs" color="gray" variant="light">
              {data?.ring_inner || '—'}
            </Badge>
          </Group>
          <Text size="xs" c="dimmed">{data?.last_switch || 'Keine Regleraktion verfügbar'}</Text>
          {data?.last_event_meta && <Text size="xs" c="dimmed">{data.last_event_meta}</Text>}
          {data?.ring_state !== 'disabled' && (
            <Progress aria-label="Mindest-Schaltintervall" value={progress} size="sm" radius="xs" color="violet" />
          )}
        </Stack>
      </Card>
    </Stack>
  );
}

export const dashboardSection: DashboardSectionDefinition = {
  id: 'controller',
  order: 20,
  Component: ControllerSection,
};
