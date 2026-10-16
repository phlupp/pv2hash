import { Card, Input, createTheme } from '@mantine/core';

/**
 * Shared design system: native Mantine components and tokens rather than
 * page-specific card styles. Future pages inherit these defaults.
 */
export const theme = createTheme({
  primaryColor: 'violet',
  defaultRadius: 'sm',
  colors: {
    dark: [
      '#f5f6fa', '#dfe2e8', '#c2c8d2', '#9aa5b5', '#727d90',
      '#566274', '#394557', '#283244', '#1b2534', '#121a28',
    ],
  },
  fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  headings: {
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  components: {
    // More contrast between editable fields and surrounding dark cards.
    // Applies to all Mantine text inputs/selects through the shared theme.
    Input: Input.extend({
      styles: {
        input: {
          backgroundColor: 'var(--mantine-color-dark-8)',
          borderColor: 'var(--mantine-color-dark-5)',
        },
      },
    }),
    Card: Card.extend({
      defaultProps: {
        withBorder: true,
        padding: 'sm',
        radius: 'sm',
        shadow: 'none',
      },
    }),
  },
});
