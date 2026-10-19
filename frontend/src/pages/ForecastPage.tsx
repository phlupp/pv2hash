import { useEffect, useState } from 'react';
import {
  Alert, Badge, Button, Card, Collapse, Divider, Group, NumberInput, PasswordInput,
  Select, Stack, Switch, Text, TextInput, Title,
} from '@mantine/core';
import { IconChevronDown, IconChevronLeft, IconChevronRight, IconRefresh } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ForecastChart, type ForecastDay } from '../forecast/ForecastChart';
import { energyValue, coverageText } from '../forecast/energy';
import { forecastSelectValue } from '../forecast/settingValues';
import { dayShift, daySelectionAfterMove, forecastDayRefreshInterval } from '../forecast/dayNavigation';
import { forecastAfterSave, syncedForecastDraft } from '../forecast/saveSync';

type Generator = { id: string; name: string; enabled: boolean; kwp: number; tilt: number; azimuth: number; loss_percent: number };
type Inverter = { id: string; name: string; generators: Generator[] };
type Config = {
  enabled: boolean; driver: string;
  drivers: { id: string; name: string }[];
  fields: { name: string; label: string; type: string;
    options?: { value: string; label: string }[];
    min?: number; max?: number; visible_when?: { name: string; value: string } }[];
  settings: { mode: string; refresh_minutes: number; resolution_minutes: number; api_key_configured: boolean; api_key?: string;
    [key: string]: string | number | boolean | undefined };
  inverters: Inverter[]; location_ready: boolean;
};
type ConfigResponse = { status: string; config: Config; history: {
  last_success?: string; last_failure?: string; last_error?: string;
} };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', ...init });
  const body = await response.json() as T & { message?: string; status?: string };
  if (!response.ok || body.status === 'error') throw new Error(body.message || 'API nicht erreichbar');
  return body;
}
const jsonPost = (data: unknown): RequestInit => ({
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
});
function num(value: string | number, fallback: number) {
  return typeof value === 'number' ? value : Number(value) || fallback;
}
const newGenerator = (): Generator => ({
  id: 'g-' + Date.now() + '-' + Math.random().toString(16).slice(2, 8),
  name: 'Neue Generatorfläche', enabled: true, kwp: 0,
  tilt: 30, azimuth: 0, loss_percent: 14,
});

export function ForecastPage() {
  const client = useQueryClient();
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [opened, setOpened] = useState(false);
  const [draft, setDraft] = useState<Config | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const day = useQuery({
    queryKey: ['forecast-day', selectedDay],
    queryFn: () => api<ForecastDay>('/api/forecast/day' + (selectedDay ? '?date=' + selectedDay : '')),
    refetchInterval: query => forecastDayRefreshInterval(
      selectedDay, query.state.data as ForecastDay | undefined,
    ),
    refetchIntervalInBackground: false,
  });
  const config = useQuery({
    queryKey: ['forecast-config'],
    queryFn: () => api<ConfigResponse>('/api/forecast/settings'),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  });
  useEffect(() => {
    // Once the editor was initialized, the old !draft check prevented further
    // server updates from appearing until a manual browser reload.
    setDraft(current => syncedForecastDraft(config.data?.config, current, dirty));
  }, [config.data, dirty]);

  function change(callback: (current: Config) => Config) {
    if (!draft) return;
    setDraft(callback(structuredClone(draft)));
    setDirty(true);
    setNotice(null);
  }
  function updateInverter(invId: string, editor: (inv: Inverter) => void) {
    change(d => { const inv = d.inverters.find(i => i.id === invId); if (inv) editor(inv); return d; });
  }
  function updateSurface(invId: string, id: string, patch: Partial<Generator>) {
    updateInverter(invId, inv => {
      inv.generators = inv.generators.map(g => g.id === id ? { ...g, ...patch } : g);
    });
  }
  async function save() {
    if (!draft || !dirty || busy) return;
    setBusy(true); setNotice(null);
    try {
      const response = await api<{status: string; config: Config}>('/api/forecast/settings', jsonPost({
        enabled: draft.enabled, driver: draft.driver,
        settings: Object.fromEntries(Object.entries(draft.settings)
          .filter(([key]) => key !== 'api_key_configured')),
        inverters: draft.inverters,
      }));
      // The POST supplies the authoritative normalized settings. Use them now,
      // even if the extra forecast retrieval takes time or fails.
      setDraft(response.config);
      client.setQueryData<ConfigResponse>(['forecast-config'], previous => ({
        status: 'ok', config: response.config, history: previous?.history ?? {},
      }));
      setDirty(false);

      // First activation should show forecast data right away. A refresh error
      // does not roll back an already saved configuration or enable miner control.
      const forecastResult = await forecastAfterSave(response.config.enabled, () =>
        api<{future_points: number; inverters: number}>(
          '/api/forecast/refresh', {method: 'POST'},
        ),
      );
      await Promise.all([
        client.invalidateQueries({queryKey: ['forecast-config']}),
        client.invalidateQueries({queryKey: ['forecast-day']}),
      ]);
      if (forecastResult.error) {
        const message = forecastResult.error instanceof Error
          ? forecastResult.error.message : String(forecastResult.error);
        setNotice('Einstellungen gespeichert, aber der Prognoseabruf ist fehlgeschlagen: ' + message);
      } else if (forecastResult.result) {
        setNotice('Einstellungen gespeichert. Prognose aktualisiert: ' +
          forecastResult.result.future_points + ' zukünftige Zeitpunkte aus ' +
          forecastResult.result.inverters + ' Wechselrichtern.');
      } else {
        setNotice('Prognoseeinstellungen gespeichert. Prognose deaktiviert. Mining-Regelung unverändert.');
      }
    } catch (error) { setNotice('Speichern fehlgeschlagen: ' + String(error)); }
    finally { setBusy(false); }
  }
  async function refresh() {
    setBusy(true); setNotice(null);
    try {
      const response = await api<{ future_points: number; inverters: number }>(
        '/api/forecast/refresh', { method: 'POST' },
      );
      setNotice('Prognose aktualisiert: ' + response.future_points +
        ' zukünftige Zeitpunkte aus ' + response.inverters + ' Wechselrichtern.');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['forecast-day'] }),
        client.invalidateQueries({ queryKey: ['forecast-config'] }),
      ]);
    } catch (error) { setNotice(String(error)); }
    finally { setBusy(false); }
  }

  const current = day.data;
  const today = current?.today;
  const showing = current?.day ?? selectedDay ?? '—';
  const saved = config.data?.config;
  const generatorCount = saved?.inverters.reduce((sum, inverter) =>
    sum + inverter.generators.filter(generator => generator.enabled).length, 0) ?? 0;
  const uncovered = saved?.inverters.filter(inverter =>
    !inverter.generators.some(generator => generator.enabled)) ?? [];
  const history = config.data?.history;
  const failureIsNewer = !!history?.last_failure && (!history.last_success ||
    Date.parse(history.last_failure) > Date.parse(history.last_success));
  const readableTime = (time: string) => new Date(time).toLocaleString('de-DE', {
    dateStyle: 'short', timeStyle: 'short',
  });
  return (
    <Stack gap="md">
      <Group justify="space-between" align="center">
        <div>
          <Title order={2} size="h3">PV-Prognose</Title>
          <Text c="dimmed" size="xs">Tagesvergleich: tatsächlich erzeugt und vorhergesagt</Text>
        </div>
        <Badge variant="light" color={saved?.enabled ? 'teal' : 'gray'}>
          {saved?.enabled ? 'Prognose aktiv' : 'Prognose inaktiv'}
        </Badge>
      </Group>
      {saved && uncovered.length > 0 && <Alert color="orange" title="PV-Anlagenmodell noch nicht vollständig">
        Für eine vollständige Prognose benötigen wir je Wechselrichter mindestens eine
        Generatorfläche mit Leistung (kWp), Dachneigung und Ausrichtung.
        Noch offen: {uncovered.map(inverter => inverter.name).join(', ')}.
        Die gemessene PV-Erzeugung ist bereits verfügbar; es werden keine Prognosewerte erfunden.
        <Group mt="sm">
          <Button variant="light" color="orange" size="xs" onClick={() => setOpened(true)}>
            Generatorflächen einrichten
          </Button>
        </Group>
      </Alert>}
      {saved && uncovered.length === 0 && generatorCount > 0 && !saved.enabled && <Alert color="blue">
        {generatorCount} Generatorfläche(n) eingerichtet. Die Prognose ist noch deaktiviert.
        <Button ml="sm" variant="light" color="blue" size="xs"
          onClick={() => setOpened(true)}>Einstellungen öffnen</Button>
      </Alert>}
      {history?.last_success && <Text size="xs" c="dimmed">
        Letzte erfolgreiche Prognoseaktualisierung: {readableTime(history.last_success)}
      </Text>}
      {failureIsNewer && history?.last_error && <Alert color="red" title="Letzte Prognoseaktualisierung fehlgeschlagen">
        {history.last_error}
      </Alert>}
      <Card withBorder padding="md">
        <Stack gap="sm">
          <div className="pv-forecast-day-nav">
            <Button className="pv-forecast-day-prev" variant="subtle" color="gray" size="sm" leftSection={<IconChevronLeft size={16}/>}
              disabled={!current || day.isLoading}
              onClick={() => today && setSelectedDay(daySelectionAfterMove(showing, today, -1))}>Zurück</Button>
            <Text className="pv-forecast-day-date" fw={650} size="sm">{current ? new Date(showing + 'T12:00:00Z').toLocaleDateString(
              'de-DE', { day: '2-digit', month: 'long', year: 'numeric', timeZone: 'UTC' },
            ) : 'Lade Tagesdaten …'}</Text>
            <Group className="pv-forecast-day-next" gap={4} wrap="nowrap">
              <Button variant="subtle" color="gray" size="sm" rightSection={<IconChevronRight size={16}/>}
                disabled={!today || showing >= dayShift(today, 1)}
                onClick={() => today && setSelectedDay(daySelectionAfterMove(showing, today, 1))}>Vor</Button>
              <Button size="sm" color={selectedDay === null ? 'violet' : 'gray'}
                variant={selectedDay === null ? 'light' : 'subtle'}
                aria-pressed={selectedDay === null}
                onClick={() => {
                  setSelectedDay(null);
                  void client.invalidateQueries({ queryKey: ['forecast-day', null] });
                }}>Heute</Button>
            </Group>
          </div>
          {day.isError && <Alert color="red">{String(day.error)}</Alert>}
          {current && <>
            <Group grow align="stretch" gap="sm">
              {([
                { key: 'forecast', label: 'Erwartete Tagesenergie', isForecast: true },
                { key: 'actual', label: showing === today ? 'Bisher erzeugte PV-Energie' : 'Erzeugte PV-Energie', isForecast: false },
              ] as const).map(item => {
                const energy = current.energy[item.key];
                const isToday = showing === today;
                return <Card key={item.key} withBorder padding="sm" radius="md"
                  aria-label={item.label}>
                  <Stack gap={2}>
                    <Text size="xs" c="dimmed">{item.label}</Text>
                    <Text fw={700} size="xl" className={'pv-forecast-energy-' + item.key}>
                      {energyValue(energy.kwh)}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {coverageText(energy, item.isForecast, isToday, showing > (today ?? showing))}
                    </Text>
                    {!energy.complete && energy.kwh !== null && <Text size="xs" c="dimmed">
                      Datenabdeckung: {energy.coverage_hours.toLocaleString('de-DE', {
                        maximumFractionDigits: 1,
                      })} von {energy.day_hours.toLocaleString('de-DE', {
                        maximumFractionDigits: 1,
                      })} Stunden
                    </Text>}
                  </Stack>
                </Card>;
              })}
            </Group>
            <ForecastChart data={current}/>
            {!current.forecast.length && <Text size="sm" c="dimmed">
              Für diesen Tag sind noch keine Prognosepunkte gespeichert. Frühere Zeitpunkte werden nicht rückwirkend aufgefüllt.
            </Text>}
            <Text size="xs" c="dimmed">
              {current.forecast.length} Prognosepunkte · {current.actual.length} Ist-Messpunkte · Zeitzone {current.timezone}
              {day.isFetching ? ' · Aktualisiere …' : ''}
            </Text>
          </>}
        </Stack>
      </Card>
      <Card withBorder padding="md">
        <Stack gap="md">
          <Group justify="space-between" align="center">
            <Button variant="subtle" color="gray" size="sm" rightSection={<IconChevronDown size={17}/>}
              onClick={() => setOpened(x => !x)} aria-expanded={opened}>
              Einstellungen
            </Button>
            <Button size="sm" variant="light" leftSection={<IconRefresh size={16}/>}
              loading={busy} disabled={!config.data?.config.enabled || dirty}
              onClick={() => void refresh()}>Prognose aktualisieren</Button>
          </Group>
          {notice && <Alert color={notice.startsWith('Speichern fehlgeschlagen:') || notice.startsWith('Error')
            ? 'red' : notice.startsWith('Einstellungen gespeichert, aber') ? 'orange' : 'blue'}>{notice}</Alert>}
          <Collapse expanded={opened}>
            {draft ? <Stack gap="md">
              <Text size="xs" c="dimmed">Die Prognose ist rein informativ und verändert keine Minerregelung.</Text>
              {!draft.location_ready && <Alert color="orange">
                Bitte zuerst Standortkoordinaten unter Einstellungen hinterlegen.
              </Alert>}
              <Switch checked={draft.enabled} label="Automatische Prognose aktivieren"
                onChange={event => change(d => { d.enabled = event.currentTarget.checked; return d; })}/>
              <Select label="Prognoseanbieter" value={draft.driver}
                data={draft.drivers.map(d => ({ value: d.id, label: d.name }))}
                onChange={value => change(d => { d.driver = value || 'open_meteo'; return d; })}/>
              {draft.fields.filter(field => !field.visible_when ||
                draft.settings[field.visible_when.name] === field.visible_when.value).map(field => (
                field.type === 'select' ? (
                  <Select key={field.name} label={field.label}
                    data={field.options || []}
                    value={String(draft.settings[field.name] ?? '')}
                    onChange={value => change(d => {
                      d.settings[field.name] = forecastSelectValue(field.name, value);
                      return d;
                    })}/>
                ) : field.type === 'password' ? (
                  <PasswordInput key={field.name} label={field.label}
                    description={draft.settings.api_key_configured
                      ? 'Schlüssel hinterlegt (leer lassen zum Beibehalten)' : 'Optionaler Provider-Schlüssel'}
                    value={String(draft.settings[field.name] ?? '')}
                    onChange={event => change(d => {
                      d.settings[field.name] = event.currentTarget.value; return d;
                    })}/>
                ) : field.type === 'number' ? (
                  <NumberInput key={field.name} label={field.label} min={field.min} max={field.max}
                    value={Number(draft.settings[field.name] ?? 0)}
                    onChange={value => change(d => {
                      d.settings[field.name] = num(value, field.min ?? 0); return d;
                    })}/>
                ) : (
                  <TextInput key={field.name} label={field.label}
                    value={String(draft.settings[field.name] ?? '')}
                    onChange={event => change(d => {
                      d.settings[field.name] = event.currentTarget.value; return d;
                    })}/>
                )
              ))}
              <Divider label="PV-Generatorflächen je Wechselrichter"/>
              <Text size="xs" c="dimmed">
                Azimut: 0° Süd · −90° Ost · +90° West. Mehrere Dachflächen pro Wechselrichter sind möglich.
                Offline-Wechselrichter bleiben für die Prognose berücksichtigt.
              </Text>
              {draft.inverters.map(inverter => (
                <Card key={inverter.id} withBorder padding="sm">
                  <Stack gap="sm">
                    <Group justify="space-between">
                      <Text fw={650} size="sm">{inverter.name}</Text>
                      <Button size="xs" variant="light" onClick={() => updateInverter(inverter.id, inv => {
                        inv.generators.push(newGenerator());
                      })}>Generatorfläche hinzufügen</Button>
                    </Group>
                    {!inverter.generators.length && <Text size="xs" c="dimmed">Noch keine Generatorfläche hinterlegt.</Text>}
                    {inverter.generators.map(generator => (
                      <Card key={generator.id} withBorder padding="sm">
                        <Stack gap="xs">
                          <Group justify="space-between" wrap="nowrap">
                            <TextInput label="Generatorfläche" value={generator.name}
                              onChange={event => updateSurface(inverter.id,generator.id,{name:event.currentTarget.value})}
                            />
                            <Button size="compact-xs" color="red" variant="subtle"
                              onClick={() => updateInverter(inverter.id, inv => {
                                inv.generators = inv.generators.filter(g => g.id !== generator.id);
                              })}>Entfernen</Button>
                          </Group>
                          <Group grow align="end">
                            <NumberInput label="Leistung (kWp)" decimalScale={2} min={0.01}
                              value={generator.kwp}
                              onChange={value => updateSurface(inverter.id,generator.id,{kwp:num(value,0)})}/>
                            <NumberInput label="Neigung (°)" min={0} max={90}
                              value={generator.tilt}
                              onChange={value => updateSurface(inverter.id,generator.id,{tilt:num(value,0)})}/>
                          </Group>
                          <Group grow align="end">
                            <NumberInput label="Azimut (°)" min={-180} max={180}
                              value={generator.azimuth}
                              onChange={value => updateSurface(inverter.id,generator.id,{azimuth:num(value,0)})}/>
                            <NumberInput label="Verlustfaktor (%)" min={0} max={99}
                              value={generator.loss_percent}
                              onChange={value => updateSurface(inverter.id,generator.id,{loss_percent:num(value,0)})}/>
                          </Group>
                          <Switch size="sm" label="Generatorfläche aktiv" checked={generator.enabled}
                            onChange={event => updateSurface(inverter.id,generator.id,{enabled:event.currentTarget.checked})}/>
                        </Stack>
                      </Card>
                    ))}
                  </Stack>
                </Card>
              ))}
              <Group justify="flex-end">
                <Button color="gray" variant="subtle" disabled={!dirty || busy} onClick={() => {
                  setDraft(config.data?.config || null); setDirty(false); setNotice(null);
                }}>Verwerfen</Button>
                <Button disabled={!dirty || busy} loading={busy} onClick={() => void save()}>
                  Einstellungen speichern
                </Button>
              </Group>
            </Stack> : <Text c="dimmed">Lade Treibereinstellungen …</Text>}
          </Collapse>
        </Stack>
      </Card>
    </Stack>
  );
}
