export type MinerField = {
  name: string;
  label: string;
  type: string;
  value?: string | number | boolean | null;
  help?: string;
  unit?: string;
  placeholder?: string;
  required?: boolean;
  read_only?: boolean;
  value_known?: boolean;
  min?: number;
  max?: number;
  step?: number | string;
  layout?: { width?: 'full' | 'half' | 'quarter' | 'third' | 'auto' };
  options?: Array<{ value: string | number; label: string }>;
};

export type MinerSummary = {
  control_text: string; control_class: string;
  connection_text: string; connection_class: string;
  runtime_state_text: string; priority_text: string;
  profile_text: string; profile_visible: boolean;
  power_text: string; power_visible: boolean;
};

export type MinerAction = {
  name: string; label: string; description?: string; confirm_text?: string;
  dangerous?: boolean; disabled_when_control_enabled?: boolean;
};
export type MinerDetail = {
  label: string;
  kind?: string;
  value?: string | number | boolean | null;
  columns?: Array<{ key: string; label: string }>;
  rows?: Array<Record<string, unknown>>;
  empty?: string;
};
export type MinerModel = {
  id: string; name: string; driver: string; driver_label: string;
  web_gui_url?: string | null;
  supports_gui_schema: boolean; control_enabled: boolean;
  summary: MinerSummary;
  identity_fields: MinerField[];
  control_fields: MinerField[];
  driver_fields: MinerField[];
  device_settings_fields: MinerField[];
  action_fields: MinerAction[];
  details?: { sections?: Array<{ title: string; items: MinerDetail[] }> };
};
export type DriverCatalogEntry = {
  key: string; label: string; supports_gui_schema: boolean; basic_fields: MinerField[];
};
export type MinersGuiPayload = {
  status: string; miners: MinerModel[];
  driver_catalog: DriverCatalogEntry[];
  core_identity_basic_fields: MinerField[];
};
export type MinersLivePayload = {
  status: string;
  miners: Array<{
    id: string;
    summary: MinerSummary;
    details?: MinerModel['details'];
  }>;
};
export type MinerValues = Record<string, string | boolean>;

export function fieldsToValues(fields: MinerField[]): MinerValues {
  return Object.fromEntries(fields.filter(f => !!f.name).map(f => [
    f.name, f.type === 'checkbox' ? !!f.value : String(f.value ?? ''),
  ]));
}
export function formForFields(fields: MinerField[], values: MinerValues): FormData {
  const form = new FormData();
  for (const field of fields) {
    if (!field.name || field.read_only) continue;
    const value = values[field.name];
    if (field.type === 'checkbox') {
      if (value === true) form.set(field.name, 'on');
    } else form.set(field.name, String(value ?? ''));
  }
  return form;
}
export function validateFields(fields: MinerField[], values: MinerValues, existing = true): string | null {
  for (const field of fields) {
    if (!field.name || field.read_only) continue;
    const v = values[field.name];
    if (field.required && (v === undefined || v === null || v === '' || (field.type === 'checkbox' && v !== true))) {
      // Masked existing passwords intentionally remain blank to mean unchanged.
      if (!(existing && field.type === 'password' && v === '')) return field.label + ' ist erforderlich.';
    }
    if (field.type === 'number' && v !== '' && v !== undefined) {
      const n = Number(v);
      if (!Number.isFinite(n) || (field.min != null && n < field.min) || (field.max != null && n > field.max)) {
        return field.label + ': Ungültiger Zahlenwert.';
      }
    }
  }
  return null;
}
