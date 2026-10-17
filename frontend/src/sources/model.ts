/** Source GUI schema emitted by each PV2Hash driver through Core API 0.9.4. */
export type SourceField = {
  name?: string;
  label?: string;
  title?: string;
  type: 'text'|'number'|'password'|'select'|'checkbox'|'hidden'|'fieldset' | string;
  value?: string | number | boolean | null;
  unit?: string;
  help?: string;
  step?: number | string;
  min?: number;
  max?: number;
  required?: boolean;
  disabled?: boolean;
  disabled_when_driver?: string;
  options?: Array<{ value: string | number; label: string }>;
  layout?: { width?: 'full'|'half'|'third'|'quarter'|'auto' };
  fields?: SourceField[];
  action_on_change?: string;
  action_on_change_busy_text?: string;
  action_on_change_empty?: 'preview'|'run'|string;
  refresh_on_change?: boolean;
};
export type SourceValue = {
  label: string;
  value: string | number | boolean | null;
  unit?: string;
  precision?: number;
};
export type SourceModel = {
  id: string;
  role: string;
  title: string;
  enabled: boolean;
  driver: string;
  driver_label: string;
  status?: { state?: string; text?: string; age_seconds?: number | null; updated_at?: string | null };
  header_fields: SourceValue[];
  detail_groups: Array<{ title: string; fields: SourceValue[] }>;
  driver_field?: SourceField;
  config_fields: SourceField[];
  actions?: Array<{ id: string; label: string; help?: string; style?: string }>;
  warnings?: string[];
};

export type SourcePayload = {
  status: string;
  message?: string;
  gui_models?: SourceModel[];
  sources?: SourceModel[];
};

export function modelsFromPayload(payload: SourcePayload): SourceModel[] {
  return payload.gui_models ?? payload.sources ?? [];
}

/**
 * Driver fields with five equal quarter-width inputs may be packed into one
 * row on wide screens. Mixed and explicitly different widths are preserved.
 */
export function isFiveFieldUniformGroup(fields: SourceField[]): boolean {
  return fields.length === 5 && fields.every(field =>
    field.type !== 'fieldset' && field.layout?.width === 'quarter',
  );
}

/**
 * Compact unopinionated groups of three like-kind inputs.
 * An explicit width from the driver always wins over this default.
 */
export function isThreeFieldCompactGroup(fields: SourceField[]): boolean {
  return fields.length === 3
    && ['text', 'number', 'select', 'password'].includes(fields[0].type)
    && fields.every(field =>
      !!field.name
      && field.type === fields[0].type
      && (!field.layout?.width || field.layout.width === 'auto'),
    );
}

export function flattenFields(fields: SourceField[]): SourceField[] {
  return fields.flatMap(field => field.type === 'fieldset' ? flattenFields(field.fields ?? []) : [field]);
}

/** Form values remain strings/bools; API interprets decimal and integral input. */
export type FormValues = Record<string, string | boolean>;
export function formValuesFromModels(models: SourceModel[]): FormValues {
  const values: FormValues = {};
  for (const model of models) {
    for (const field of flattenFields([...(model.driver_field ? [model.driver_field] : []), ...model.config_fields])) {
      if (!field.name) continue;
      values[field.name] = field.type === 'checkbox' ? Boolean(field.value) : String(field.value ?? '');
    }
  }
  return values;
}

export function toFormData(values: FormValues): FormData {
  const data = new FormData();
  for (const [name, value] of Object.entries(values)) {
    if (typeof value === 'boolean') { if (value) data.set(name, 'on'); }
    else data.set(name, value);
  }
  return data;
}

export function formatSourceValue(value: SourceValue): string {
  if (value.value === null || value.value === undefined || value.value === '') return '—';
  const raw = typeof value.value === 'number' && typeof value.precision === 'number'
    ? value.value.toLocaleString('de-DE', { minimumFractionDigits: value.precision, maximumFractionDigits: value.precision })
    : typeof value.value === 'number'
      ? value.value.toLocaleString('de-DE', { maximumFractionDigits: 2 })
      : String(value.value);
  return raw + (value.unit ? ' ' + value.unit : '');
}

export function validateSourceValues(models: SourceModel[], values: FormValues): string[] {
  const errors: string[] = [];
  for (const model of models) {
    for (const field of flattenFields([...(model.driver_field ? [model.driver_field] : []), ...model.config_fields])) {
      if (!field.name || field.type === 'hidden' || field.disabled || field.disabled_when_driver === model.driver) continue;
      const value = values[field.name];
      const empty = value === undefined || value === null || value === '';
      if (field.required && (empty || (field.type === 'checkbox' && value !== true))) {
        errors.push(`${model.title} – ${field.label || field.name}: Pflichtfeld.`);
        continue;
      }
      if (empty || field.type !== 'number') continue;
      const number = Number(value);
      if (!Number.isFinite(number) || (typeof field.min === 'number' && number < field.min)
        || (typeof field.max === 'number' && number > field.max)) {
        errors.push(`${model.title} – ${field.label || field.name}: Ungültiger Zahlenwert.`);
      }
    }
  }
  return errors;
}
