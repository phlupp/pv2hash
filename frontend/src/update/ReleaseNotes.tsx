import { Box, Group, Stack, Text } from '@mantine/core';
import { parseReleaseNotes } from './releaseNotes';

/** Human-readable GitHub release text displayed inside the System update card. */
export function ReleaseNotes({ markdown }: { markdown: string }) {
  const blocks = parseReleaseNotes(markdown);
  if (!blocks.length) return <Text size="sm" c="dimmed">Keine Release Notes vorhanden.</Text>;
  return (
    <Stack gap={5} mt="xs">
      {blocks.map((block, index) => {
        if (block.type === 'heading') {
          return <Text key={index} size="sm" fw={650} mt="xs">{block.text}</Text>;
        }
        if (block.type === 'bullet') {
          return (
            <Group key={index} align="flex-start" gap="xs" wrap="nowrap">
              <Text size="sm" c="violet.3" style={{ flexShrink: 0 }}>•</Text>
              <Box style={{ minWidth: 0, flex: 1 }}>
                <Text size="sm" style={{ overflowWrap: 'anywhere' }}>{block.text}</Text>
              </Box>
            </Group>
          );
        }
        return <Text key={index} size="sm" c="dimmed" style={{ overflowWrap: 'anywhere' }}>{block.text}</Text>;
      })}
    </Stack>
  );
}
