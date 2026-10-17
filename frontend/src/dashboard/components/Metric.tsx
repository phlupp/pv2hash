import { Card, Group, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

export function Metric({
  label, value, detail, accent, quality,
}: {
  label: string;
  value: string;
  detail?: ReactNode;
  accent?: 'teal' | 'orange';
  quality?: ReactNode;
}) {
  return (
    <Card>
      <Stack gap={3}>
        <Group align="center" justify="space-between" gap={4} wrap="wrap">
          <Text size="xs" fw={600} c="dimmed">{label}</Text>
          {quality}
        </Group>
        <Text
          size="xl"
          fw={700}
          lh={1.2}
          c={accent ?? undefined}
          style={{ fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}
        >{value}</Text>
        {detail && <Text component="div" size="xs" c="dimmed">{detail}</Text>}
      </Stack>
    </Card>
  );
}
