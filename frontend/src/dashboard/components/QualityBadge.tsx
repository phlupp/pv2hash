import { Badge, Tooltip } from '@mantine/core';

type QualityDisplay = {
  label: string;
  color: 'teal' | 'blue' | 'yellow' | 'red' | 'gray';
};

const qualityStates: Record<string, QualityDisplay> = {
  live: { label: 'LIVE', color: 'teal' },
  ok: { label: 'OK', color: 'teal' },
  good: { label: 'OK', color: 'teal' },
  simulated: { label: 'SIMULIERT', color: 'blue' },
  standby: { label: 'STANDBY', color: 'gray' },
  stale: { label: 'VERALTET', color: 'yellow' },
  degraded: { label: 'GESTÖRT', color: 'yellow' },
  no_data: { label: 'KEINE DATEN', color: 'gray' },
  disabled: { label: 'DEAKTIVIERT', color: 'gray' },
  offline: { label: 'OFFLINE', color: 'red' },
  unavailable: { label: 'OFFLINE', color: 'red' },
  error: { label: 'FEHLER', color: 'red' },
  unknown: { label: 'UNBEKANNT', color: 'gray' },
};

/** Shows the measurement's own status. Never reuse another source's quality. */
export function QualityBadge({
  quality,
  source,
  measured,
  missingQualityHint,
}: {
  quality?: string | null;
  source?: string | null;
  measured: boolean;
  missingQualityHint?: string;
}) {
  const normalized = quality?.toLowerCase().trim() || '';
  const display: QualityDisplay = normalized
    ? qualityStates[normalized] ?? { label: 'UNBEKANNT', color: 'gray' }
    : measured
      ? { label: 'OHNE STATUS', color: 'gray' }
      : { label: 'KEIN WERT', color: 'gray' };

  const statusText = normalized ? `Messstatus: ${quality}` : measured
    ? (missingQualityHint ?? 'Für diesen Messwert wird kein eigener Qualitätsstatus geliefert.')
    : 'Aktuell kein Messwert verfügbar.';
  const sourceText = source ? `Quelle: ${source}. ` : '';

  return (
    <Tooltip
      label={`${sourceText}${statusText}`}
      withArrow
      multiline
      maw={260}
      position="top"
    >
      <Badge
        size="xs"
        color={display.color}
        variant="light"
        radius="sm"
        aria-label={`Messqualität: ${display.label}`}
        tabIndex={0}
        style={{ flexShrink: 0, cursor: 'help' }}
      >
        {display.label}
      </Badge>
    </Tooltip>
  );
}
