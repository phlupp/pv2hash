import { Card, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

export function Metric({
  label, value, detail, accent,
}: {
  label: string;
  value: string;
  detail?: ReactNode;
  accent?: 'teal' | 'orange';
}) {
  return (
    <Card>
      <Stack gap={3}>
        <Text size="xs" fw={600} c="dimmed">{label}</Text>
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
