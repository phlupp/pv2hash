import { useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { IconAlertCircle, IconRefresh } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getSourcesStatus, previewSourcesConfig, runSourceAction, saveSourcesConfig } from '../sources/api';
import { SourceCard } from '../sources/SourceCard';
import {
  formValuesFromModels, modelsFromPayload, toFormData, validateSourceValues,
  type FormValues, type SourceField, type SourceModel, type SourcePayload,
} from '../sources/model';

/**
 * A source driver contributes a JSON GUI model; React only renders it.
 * No SMA, Modbus or simulator-specific form branches belong here.
 */
export function Measurements() {
  const queryClient = useQueryClient();
  const live = useQuery({
    queryKey: ['sources', 'status'],
    queryFn: getSourcesStatus,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
  });
  const [draftModels, setDraftModels] = useState<SourceModel[] | null>(null);
  const [draftValues, setDraftValues] = useState<FormValues>({});
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [dirtyIds, setDirtyIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (!live.data || initialized.current) return;
    const models = modelsFromPayload(live.data);
    setDraftModels(models);
    setDraftValues(formValuesFromModels(models));
    initialized.current = true;
  }, [live.data]);

  useEffect(() => {
    function warnLeave(event: BeforeUnloadEvent) {
      if (!dirty) return;
      event.preventDefault();
    }
    window.addEventListener('beforeunload', warnLeave);
    return () => window.removeEventListener('beforeunload', warnLeave);
  }, [dirty]);

  function mergeDraft(payload: SourcePayload, isSaved: boolean) {
    const models = modelsFromPayload(payload);
    setDraftModels(models);
    setDraftValues(formValuesFromModels(models));
    setDirty(!isSaved);
    if (isSaved) setDirtyIds([]);
    setError(null);
  }

  async function doPreview(
    nextValues: FormValues,
    source: SourceModel,
    field: SourceField,
  ) {
    if (busy) return;
    const actionId = field.action_on_change;
    if (actionId && nextValues[field.name || ''] === '' && field.action_on_change_empty !== 'run') {
      if (field.action_on_change_empty !== 'preview') return;
    }
    setBusy(true);
    try {
      let payload: SourcePayload;
      if (actionId && (nextValues[field.name || ''] !== '' || field.action_on_change_empty === 'run')) {
        const form = toFormData(nextValues);
        form.set('source_id', source.id);
        form.set('action_id', actionId);
        payload = await runSourceAction(form);
        notifications.show({ message: payload.message || 'Treiberaktion abgeschlossen.', color: 'teal' });
      } else {
        payload = await previewSourcesConfig(toFormData(nextValues));
      }
      mergeDraft(payload, false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Vorschau fehlgeschlagen.';
      setError(message);
      notifications.show({ message, color: 'red' });
    } finally {
      setBusy(false);
    }
  }

  function updateField(field: SourceField, source: SourceModel, value: string | boolean) {
    const name = field.name;
    if (!name || busy) return;
    const next = { ...draftValues, [name]: value };
    setDraftValues(next);
    setDirty(true);
    setDirtyIds(old => old.includes(source.id) ? old : [...old, source.id]);
    setError(null);
    if (field.refresh_on_change || field.action_on_change) {
      void doPreview(next, source, field);
    }
  }

  async function action(source: SourceModel, actionId: string) {
    if (busy) return;
    setBusy(true);
    try {
      const form = toFormData(draftValues);
      form.set('source_id', source.id);
      form.set('action_id', actionId);
      const payload = await runSourceAction(form);
      mergeDraft(payload, false);
      setDirtyIds(old => old.includes(source.id) ? old : [...old, source.id]);
      notifications.show({ message: payload.message || 'Aktion abgeschlossen.', color: 'teal' });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Geräteaktion fehlgeschlagen.';
      setError(message);
      notifications.show({ color: 'red', message });
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!dirty || busy || !draftModels) return;
    const validation = validateSourceValues(draftModels, draftValues);
    if (validation.length) {
      setError(validation[0]);
      notifications.show({ message: validation[0], color: 'red' });
      return;
    }
    setBusy(true);
    try {
      const payload = await saveSourcesConfig(toFormData(draftValues));
      mergeDraft(payload, true);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sources'] }),
        queryClient.invalidateQueries({ queryKey: ['status'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      ]);
      notifications.show({ message: payload.message || 'Messungen gespeichert.', color: 'teal' });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Messungen konnten nicht gespeichert werden.';
      setError(message);
      notifications.show({ message, color: 'red' });
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    const models = modelsFromPayload(live.data ?? {status:'ok'});
    if (!models.length || busy) return;
    setDraftModels(models);
    setDraftValues(formValuesFromModels(models));
    setDirty(false);
    setDirtyIds([]);
    setError(null);
  }

  const actual = modelsFromPayload(live.data ?? { status: 'ok' });
  return (
    <Stack gap="md">
      <Group justify="space-between" align="center" gap="xs">
        <div>
          <Title order={2} size="h3">Messungen</Title>
          <Text size="xs" c="dimmed">Messquellen und ihre Treibereinstellungen · Live-Aktualisierung</Text>
        </div>
        <Group gap="xs">
          <Badge variant="light" color={live.isError ? 'red' : 'teal'} size="xs">
            {live.isError ? 'Offline' : live.isPending ? 'Verbinde …' : 'Live'}
          </Badge>
          <Button leftSection={<IconRefresh size={15} />} variant="subtle" color="gray" size="compact-sm"
            disabled={busy} onClick={() => void live.refetch()}>Aktualisieren</Button>
        </Group>
      </Group>

      {live.isError && <Alert color="red" title="Messdaten nicht erreichbar" icon={<IconAlertCircle size={18} />}>{live.error.message}</Alert>}
      {error && <Alert color="red" title="Treiberaktion fehlgeschlagen" icon={<IconAlertCircle size={18} />}>{error}</Alert>}
      {dirty && (
        <Group gap="xs" justify="space-between">
          <Text size="xs" c="orange">Nicht gespeicherte Änderungen an den Messquellen.</Text>
          <Group gap="xs">
            <Button variant="subtle" color="gray" size="compact-sm" disabled={busy} onClick={reset}>Verwerfen</Button>
            <Button size="compact-sm" loading={busy} onClick={() => void save()}>Alle Änderungen speichern</Button>
          </Group>
        </Group>
      )}

      {draftModels === null ? (
        <Group py="xl" gap="sm"><Loader size="sm" /><Text c="dimmed">Treibermodelle werden geladen …</Text></Group>
      ) : (
        <Stack gap="sm">
          {draftModels.map(model => (
            <SourceCard
              key={model.id}
              model={model}
              liveModel={actual.find(item => item.id === model.id)}
              expanded={expandedIds.includes(model.id)}
              values={draftValues}
              dirty={dirtyIds.includes(model.id)}
              busy={busy}
              onToggle={() => setExpandedIds(old => old.includes(model.id) ? old.filter(id => id !== model.id) : [...old, model.id])}
              onFieldChange={updateField}
              onAction={(s, id) => void action(s, id)}
              onSave={() => void save()}
            />
          ))}
          {!draftModels.length && <Text c="dimmed">Keine Messquellen konfiguriert.</Text>}
        </Stack>
      )}
    </Stack>
  );
}
