import { Badge, Card, Group, Stack, Text, Title } from '@mantine/core';
import { IconTools } from '@tabler/icons-react';

export function Placeholder({ title, description, future = false }: { title: string; description: string; future?: boolean }) {
  return (
    <Stack gap="sm">
      <div>
        <Title order={2} size="h3">{title}</Title>
        <Text c="dimmed" size="xs">{description}</Text>
      </div>
      <Card>
        <Group gap="sm" align="flex-start">
          <IconTools size={21} stroke={1.5} />
          <Stack gap={4} style={{ flex: 1 }}>
            <Text fw={600} size="sm">{future ? 'Vorgemerktes Modul' : 'Migration vorbereitet'}</Text>
            <Text size="sm" c="dimmed">
              {future
                ? 'Hier wird später das neue PV2Hash-Modul eingebunden.'
                : 'Diese Seite wird migriert. Bis dahin ist die Funktion weiterhin in der bisherigen PV2Hash-GUI verfügbar.'}
            </Text>
            <Badge w="fit-content" size="xs" variant="outline" color="gray">Noch keine Aktionen verfügbar</Badge>
          </Stack>
        </Group>
      </Card>
    </Stack>
  );
}
