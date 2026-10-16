import { Badge, Card, Stack, Text, Title } from '@mantine/core';
import { IconTools } from '@tabler/icons-react';

export function Placeholder({ title, description, future = false }: { title: string; description: string; future?: boolean }) {
  return (
    <Stack gap="lg">
      <div>
        <Title order={1}>{title}</Title>
        <Text c="dimmed" size="sm" mt={6}>{description}</Text>
      </div>
      <Card className="pv-card" padding="xl">
        <Stack align="flex-start" gap="md">
          <IconTools size={30} color="#a47bff" stroke={1.5} />
          <Title order={3}>{future ? 'Vorgemerktes Modul' : 'Migration vorbereitet'}</Title>
          <Text c="dimmed">
            {future
              ? 'Hier wird später das neue PV2Hash-Modul eingebunden.'
              : 'Dieser Bereich wird auf die neue Komponentenarchitektur migriert. Die vollständige Funktion steht weiterhin in der bisherigen PV2Hash-GUI bereit.'}
          </Text>
          <Badge variant="outline" color="gray">Noch keine Aktionen verfügbar</Badge>
        </Stack>
      </Card>
    </Stack>
  );
}
