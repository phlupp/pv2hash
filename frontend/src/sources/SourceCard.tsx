import {
  Badge, Box, Button, Card, Checkbox, Collapse, Divider, Grid, Group, Select,
  SimpleGrid, Stack, Text, TextInput, Tooltip,
} from '@mantine/core';
import { IconChevronDown, IconChevronRight, IconDeviceFloppy } from '@tabler/icons-react';
import { QualityBadge } from '../dashboard/components/QualityBadge';
import { formatSourceValue, isFiveFieldUniformGroup, type FormValues, type SourceField, type SourceModel } from './model';

const columnSpan: Record<string, number> = {
  full: 12, half: 6, third: 4, quarter: 3, auto: 3,
};

function fieldSpan(field: SourceField): number {
  return columnSpan[field.layout?.width ?? 'full'] || 12;
}

type FieldRendererProps = {
  field: SourceField;
  source: SourceModel;
  values: FormValues;
  disabled: boolean;
  onFieldChange: (field: SourceField, source: SourceModel, value: string | boolean) => void;
  fiveColumnGroup?: boolean;
};

function FieldRenderer({ field, source, values, disabled, onFieldChange, fiveColumnGroup = false }: FieldRendererProps) {
  if (field.type === 'fieldset') {
    return (
      <Grid.Col span={12}>
        <Stack gap="xs" mt="xs">
          <Divider label={field.label || field.title || 'Einstellungen'} labelPosition="left" />
          <Grid gap="xs" columns={isFiveFieldUniformGroup(field.fields ?? []) ? 20 : 12}>
            {(field.fields ?? []).map((child, i) => (
              <FieldRenderer
                key={child.name || `fieldset-${i}`} field={child} source={source}
                values={values} disabled={disabled} onFieldChange={onFieldChange}
                fiveColumnGroup={isFiveFieldUniformGroup(field.fields ?? [])}
              />
            ))}
          </Grid>
          {field.help && <Text size="xs" c="dimmed">{field.help}</Text>}
        </Stack>
      </Grid.Col>
    );
  }
  if (!field.name || field.type === 'hidden') return null;
  if (!['checkbox','select','text','number','password'].includes(field.type)) {
    return <Grid.Col span={12}><Text size="xs" c="orange">Nicht unterstützter Feldtyp: {field.type} ({field.label || field.name})</Text></Grid.Col>;
  }

  const inactive = disabled || Boolean(field.disabled) || field.disabled_when_driver === source.driver;
  const value = values[field.name] ?? (field.type === 'checkbox' ? false : '');
  const common = {
    label: field.label || field.name,
    description: field.help,
    required: field.required,
    disabled: inactive,
    size: 'sm' as const,
  };
  const label = field.unit ? `${common.label} (${field.unit})` : common.label;

  return (
    <Grid.Col span={fiveColumnGroup
      ? { base: 20, sm: 10, md: 5, lg: 4 }
      : { base: 12, sm: fieldSpan(field) }}>
      {field.type === 'checkbox' ? (
        <Box pt="xs">
          <Checkbox
            label={label}
            description={common.description}
            checked={value === true}
            disabled={inactive}
            onChange={e => onFieldChange(field, source, e.currentTarget.checked)}
          />
        </Box>
      ) : field.type === 'select' ? (
        <Select
          {...common}
          label={label}
          description={field.options?.length ? field.help : undefined}
          placeholder={!field.options?.length
            ? field.help || 'Keine Einträge verfügbar'
            : field.required ? 'Bitte auswählen …' : 'Auswählen …'}
          data={(field.options || []).map(option => ({ value: String(option.value), label: option.label }))}
          value={String(value) || null}
          onChange={next => onFieldChange(field, source, next ?? '')}
          searchable={(field.options?.length ?? 0) > 5}
          allowDeselect={false}
        />
      ) : (
        <TextInput
          {...common}
          label={label}
          type={field.type === 'number' ? 'number' : field.type === 'password' ? 'password' : 'text'}
          value={String(value)}
          step={field.step}
          min={field.min}
          max={field.max}
          autoComplete={field.type === 'password' ? 'off' : undefined}
          onChange={e => onFieldChange(field, source, e.currentTarget.value)}
        />
      )}
    </Grid.Col>
  );
}

export function SourceCard({
  model, liveModel, expanded, values, dirty, busy, onToggle, onFieldChange, onAction, onSave,
}: {
  model: SourceModel;
  liveModel?: SourceModel;
  expanded: boolean;
  values: FormValues;
  dirty: boolean;
  busy: boolean;
  onToggle: () => void;
  onFieldChange: (field: SourceField, source: SourceModel, value: string | boolean) => void;
  onAction: (model: SourceModel, actionId: string) => void;
  onSave: () => void;
}) {
  const previewOnly = !!liveModel && liveModel.driver !== model.driver;
  const live = !previewOnly && liveModel ? liveModel : model;
  const fieldSet = [
    ...(model.driver_field ? [{ ...model.driver_field, refresh_on_change: true }] : []),
    ...model.config_fields,
  ];
  const quality = live.status?.state;
  return (
    <Card component="section" aria-label={model.title} padding="md">
      <Stack gap="sm">
        <Box>
          <Group justify="space-between" gap="xs" align="flex-start">
            <Box style={{ flex: 1, minWidth: 0 }}>
              <Group gap="sm" wrap="wrap">
                <Text fw={650} size="md">{model.title}</Text>
                {previewOnly
                  ? <Tooltip label="Ungespeicherte Treibervorschau – der bisherige Treiber läuft weiter." withArrow>
                      <Badge color="gray" size="xs" variant="light">VORSCHAU</Badge>
                    </Tooltip>
                  : <QualityBadge
                      quality={quality}
                      measured={quality !== 'disabled' && quality !== 'unknown'}
                      source={model.driver_label}
                    />}

                {dirty && <Badge color="orange" variant="light" size="xs">Ungespeichert</Badge>}
              </Group>
              <Text size="xs" c="dimmed" mt={2}>{model.driver_label || model.driver}</Text>
            </Box>
            <Button
              size="compact-sm" variant="subtle" color="gray"
              rightSection={expanded ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
              onClick={onToggle} aria-expanded={expanded}
            >
              {expanded ? 'Schließen' : 'Details'}
            </Button>
          </Group>
          <Group gap={6} mt="sm" align="flex-start">
            {(live.header_fields || []).filter(field => field.label.toLowerCase() !== 'status').map((field, i) => (
              <Tooltip key={`${field.label}-${i}`} label={field.label} withArrow>
                <Badge variant="light" color="gray" radius="sm" size="sm" style={{ height: 'auto', minHeight: 23, paddingBlock: 4, textTransform: 'none' }}>
                  <Text span size="xs" c="dimmed" fw={400}>{field.label}: </Text>
                  <Text span size="xs" fw={600}>{formatSourceValue(field)}</Text>
                </Badge>
              </Tooltip>
            ))}
          </Group>
        </Box>
        <Collapse expanded={expanded}>
          <Stack gap="sm">
            {!!live.detail_groups?.length && (
              <Card withBorder={false} padding="sm" bg="dark.7">
                <Text size="sm" fw={600} mb="xs">Messdetails</Text>
                {live.detail_groups.map((group, idx) => (
                  <Stack key={idx} gap="xs">
                    {live.detail_groups.length > 1 && <Text size="xs" c="dimmed">{group.title}</Text>}
                    <SimpleGrid cols={{
                      base: 2,
                      sm: Math.min(group.fields.length, 3),
                      md: Math.min(group.fields.length, 4),
                      lg: Math.min(group.fields.length, 6),
                    }} spacing="xs">
                      {group.fields.map((field, i) => (
                        <div key={`${field.label}-${i}`}>
                          <Text size="xs" c="dimmed">{field.label}</Text>
                          <Text fw={600} size="sm">{formatSourceValue(field)}</Text>
                        </div>
                      ))}
                    </SimpleGrid>
                  </Stack>
                ))}
              </Card>
            )}
            <Text fw={600} size="sm">Konfiguration</Text>
            <Grid gap="xs">
              {fieldSet.map((field, i) => (
                <FieldRenderer
                  key={field.name || `group-${i}`}
                  field={field} source={model} values={values}
                  disabled={busy} onFieldChange={onFieldChange}
                />
              ))}
            </Grid>
            {!!model.warnings?.length && model.warnings.map((warning, i) => (
              <Text key={i} size="xs" c="orange">{warning}</Text>
            ))}
            <Group justify="space-between" gap="xs">
              <Group gap="xs">
                {(model.actions ?? []).map(action => (
                  <Button key={action.id} color={action.style === 'danger' ? 'red' : 'gray'} variant="light" size="sm"
                    title={action.help} disabled={busy}
                    onClick={() => onAction(model, action.id)}
                  >{action.label}</Button>
                ))}
              </Group>
              <Button leftSection={<IconDeviceFloppy size={17} />} loading={busy}
                size="sm" onClick={onSave} disabled={!dirty}>Speichern</Button>
            </Group>
          </Stack>
        </Collapse>
      </Stack>
    </Card>
  );
}
