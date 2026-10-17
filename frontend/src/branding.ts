import type {SettingsResponse} from './configuration/types';

/** The saved instance name is part of the settings GUI schema, not a hard-coded label. */
export function instanceNameFromSettings(settings?: SettingsResponse): string {
  const field = settings?.model.sections.flatMap(s => s.fields).find(f => f.name === 'instance_name');
  return String(field?.value ?? settings?.instance_name ?? '').trim();
}

export function browserTitle(instanceName: string): string {
  return instanceName.trim() ? `PV2Hash - ${instanceName.trim()}` : 'PV2Hash';
}
