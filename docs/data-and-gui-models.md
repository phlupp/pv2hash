# PV2Hash Data and GUI Models

PV2Hash uses driver-defined data models. The frontend should render these models generically and must not contain driver-specific field names or special cases.

This document describes the shared GUI model, the miner data model, and the source data model.

## 1. GUI Model / Layout Model

The GUI model describes how driver-defined fields, details, badges, and actions are rendered.

### Field Model

A field describes one editable value.

```json
{
  "name": "host",
  "label": "Host/IP",
  "type": "text",
  "value": "192.168.1.10",
  "required": true,
  "unit": null,
  "help": "Optional help text",
  "options": [],
  "layout": {
    "width": "half"
  }
}
```

### Field properties

| Property | Required | Description |
|---|---:|---|
| `name` | yes | Stable internal field name used for saving. |
| `label` | yes | Human-readable label. |
| `type` | yes | Input type, for example `text`, `number`, `select`, `checkbox`, `password`. |
| `value` | no | Current value. |
| `required` | no | Marks the field as required. The GUI renders a required marker. |
| `unit` | no | Unit rendered next to the label, for example `W`, `A`, `V`, `%`, `ms`, `s`, `kWh`. |
| `help` | no | Explanatory text. Do not use this for units. |
| `options` | only for select | Select options. |
| `layout` | no | Layout metadata. Missing layout means default rendering. |

### Supported field types

| Type | Description |
|---|---|
| `text` | Single-line text input. |
| `number` | Numeric input. |
| `select` | Dropdown selection using `options`. |
| `checkbox` | Boolean value. |
| `password` | Sensitive text input. |
| `hidden` | Internal value, not visible to the user. |

Additional field types may be added later, but must be handled globally by the renderer.

### Select options

```json
{
  "name": "driver",
  "label": "Profil",
  "type": "select",
  "value": "sma_meter",
  "options": [
    {"value": "simulator", "label": "Simulierter Netzanschlusspunkt"},
    {"value": "sma_meter", "label": "SMA Energy Meter"}
  ]
}
```

### Layout metadata

```json
{
  "layout": {
    "width": "half"
  }
}
```

Supported widths:

| Width | Meaning |
|---|---|
| `full` | Full row width. |
| `half` | Half row width on desktop. |
| `third` | One third row width on desktop. |
| `quarter` | One quarter row width on desktop. |
| `auto` | Compact automatic width where supported. |

The renderer must handle responsive behavior globally. On small screens fields should stack vertically.

### Required marker

If `required` is true, the GUI renders the field as mandatory, for example with a red `*` next to the label.

### Units

Units must be provided via `unit`, not embedded in `label` or `help`.

Correct:

```json
{"label": "Poll-Intervall", "unit": "ms"}
```

Incorrect:

```json
{"label": "Poll-Intervall (ms)"}
```

### Header fields

Header fields are compact spot values shown directly in a card header.

Examples:

```json
{"label": "Status", "value": "Live", "variant": "ok"}
{"label": "Alter", "value": "1.2 s"}
{"label": "Leistung", "value": "-1240 W"}
{"label": "SoC", "value": "82 %"}
```

Header fields should only contain important live overview values. They must not duplicate all detail values.

### Detail groups

Detail groups are read-only runtime details.

```json
{
  "title": "Details",
  "fields": [
    {"label": "Frequenz", "value": 50.01, "unit": "Hz"},
    {"label": "L1 Spannung", "value": 230.4, "unit": "V"}
  ]
}
```

Rules:

- Detail groups are optional.
- If no details are available, no details card should be rendered.
- Values already shown as important header spot values should not be duplicated unless there is a strong reason.
- The GUI renders details generically.

### Actions

Actions are driver-defined buttons rendered by the GUI.

```json
{
  "name": "discover_devices",
  "label": "Geräte-Suche",
  "variant": "secondary",
  "description": "Sucht verfügbare Geräte im lokalen Netzwerk."
}
```

Action rules:

- Actions belong to a driver or source/miner card.
- The GUI renders the button and calls the generic action endpoint.
- The driver implements the action.
- After an action, the GUI may re-render the affected model.
- Actions must not require hard-coded frontend logic.

Example: The SMA source uses `Geräte-Suche` to collect SMA Energy Meter telegrams and refresh the serial-number dropdown.

## 2. Miner Data Model

Miner drivers describe miner configuration, runtime state, details, and device actions.

### Miner responsibilities

A miner driver should provide:

- Driver identity and label.
- Configuration fields.
- Device settings fields if supported.
- Runtime status.
- Header fields / status badges.
- Detail groups.
- Actions where needed.

### Miner runtime model

Typical runtime values:

| Field | Description |
|---|---|
| `id` | Internal miner id. |
| `name` | Display name. |
| `driver` | Driver key. |
| `driver_label` | Human-readable driver name. |
| `enabled` | Whether the miner is enabled in PV2Hash. |
| `reachable` | Whether the miner can currently be reached. |
| `running` | Whether mining is currently active. |
| `profile` | Current/target profile, for example `off`, `eco`, `mid`, `high`. |
| `power_w` | Current power estimate in W. |
| `hashrate` | Current hashrate if available. |
| `last_error` | Last driver error if any. |

### Miner config fields

Miner config fields use the shared field model.

Examples:

```json
{"name": "host", "label": "Host/IP", "type": "text", "required": true, "layout": {"width": "half"}}
{"name": "priority", "label": "Priorität", "type": "number", "required": true, "layout": {"width": "quarter"}}
```

### Miner device settings

Device settings are driver-defined and use the same field model.

Examples:

- Power limit.
- Fan settings.
- Driver-specific behavior flags.

The GUI must not know device-specific field names.

### Miner details

Miner details are read-only and rendered as detail groups.

Examples:

- API version.
- Firmware/platform.
- Hashboards.
- Power target / constraints.
- Fan state.
- Driver-specific status.

### Miner actions

Possible miner actions:

- Start/resume mining.
- Pause mining.
- Reboot device.
- Driver-specific device actions.

Actions should follow the shared action model where possible.

## 3. Sources Data Model

Source drivers describe data inputs used by PV2Hash, such as grid meters, batteries, and future source roles.

### Source roles

Sources are role-based. Current roles:

| Role | Description |
|---|---|
| `grid` | Grid connection / net power measurement. Usually exactly one active source. |
| `battery` | Battery state and power data. Usually exactly one active source. |

Future roles may include:

| Role | Description |
|---|---|
| `socket` | Smart socket / external load / controllable or measurable consumer. |

The model must not be hard-limited to `grid` and `battery`.

### Source responsibilities

A source driver should provide:

- Role.
- Driver key and label.
- Configuration fields.
- Header fields.
- Optional detail groups.
- Optional actions.
- Runtime quality/status.

### Source GUI model

```json
{
  "id": "grid",
  "role": "grid",
  "title": "Netz-Messung",
  "driver": "sma_meter",
  "driver_label": "SMA Energy Meter",
  "header_fields": [],
  "detail_groups": [],
  "config_fields": [],
  "actions": []
}
```

### Source status / quality

Sources should expose a quality state that can be rendered in the header.

Common states:

| State | Meaning |
|---|---|
| `live` | Data is current and valid. |
| `stale` | Last data exists but is too old. |
| `offline` | Required data cannot be read. |
| `disabled` | Source is disabled or not configured. |
| `error` | Driver error. |

### Grid source model

A grid source should provide the current grid power.

Common runtime values:

| Value | Unit | Description |
|---|---|---|
| `grid_power_w` | W | Net power at grid connection point. Negative usually means export, positive usually means import. |
| `age` | s | Age of last valid measurement. |

SMA-specific optional details:

| Value | Unit |
|---|---|
| Frequency | Hz |
| L1/L2/L3 voltage | V |
| L1/L2/L3 current | A |
| L1/L2/L3 power | W |

SMA-specific action:

| Action | Description |
|---|---|
| `discover_devices` / `Geräte-Suche` | Starts a short discovery to collect SMA Energy Meter telegrams and refresh serial-number options. |

SMA rules:

- Serial number selection may be required for robust filtering.
- If no devices are known, show a hint such as `Bitte Geräte-Suche starten.`
- Local interface/IP should be provided as a driver-defined dropdown, usually including `0.0.0.0` plus detected local IPv4 interfaces.
- The frontend must not contain SMA-specific discovery or serial-number logic.

### Battery source model

A battery source should provide values required by PV2Hash control logic.

Required values:

| Value | Unit | Description |
|---|---|---|
| SoC | % | Battery state of charge. |
| Charge power | W | Current charging power. |
| Discharge power | W | Current discharging power. |

Optional monitoring values:

| Value | Unit |
|---|---|
| Voltage | V |
| Current | A |
| SOH | % |
| Temperature | °C |
| Nominal capacity | kWh |
| Max charge current | A |
| Max discharge current | A |

Battery Modbus rules:

- Required registers must be configured and readable.
- If a required register fails, the battery source quality must become offline/disconnected.
- Optional registers may be empty.
- Empty optional registers must not be queried.
- If an optional register fails, the error may be ignored or logged compactly, but it must not break the source.
- Optional values should only be shown in details if a valid value exists.

### Source profile / driver switching

For single-role sources like `grid` and `battery`, the selected driver/profile determines the config schema.

Rules:

- Changing the profile in the GUI should immediately render the config fields for the newly selected driver.
- This must happen without saving first.
- Live refresh must not overwrite active user edits in config fields.
- Config should only be re-rendered on initial load, explicit profile change, save, or action result.

## 4. General Rules

- Drivers define fields, labels, required state, units, layout, details, and actions.
- The GUI renders models generically.
- The GUI must not hard-code driver-specific field names.
- Source and miner renderers should share global field rendering behavior where possible.
- Optional values must not break polling/control flow.
- Required values may set the device/source state to error/offline if unavailable.
- Live refresh should update runtime/header/detail values, not active form inputs.
- New fields should prefer model metadata over custom frontend logic.

## Modbus battery profiles

The Modbus TCP battery source may offer optional device presets. These presets are not part of the global GUI model; they are a driver-specific convenience for the universal Modbus battery driver.

Profiles are loaded from:

```text
pv2hash/modbus_profiles/battery/*.yaml
/var/lib/pv2hash/modbus_profiles/battery/*.yaml
```

The first path is intended for profiles shipped with PV2Hash. The second path is intended for user-provided profiles that should survive updates.

A profile only pre-fills configuration values. It does not define field labels, units, required state, layout, validation, or system behavior. Those remain owned by the driver.

Example structure:

```yaml
id: example_battery
name: Example Battery BMS
vendor: ExampleVendor
hidden: true

values:
  port: 502
  unit_id: 1
  poll_interval_ms: 1000
  timeout_ms: 800

  soc:
    address: 100
    register_type: holding
    type: uint16
    endian: big_endian
    factor: 0.1

  charge_power:
    address: 101
    register_type: holding
    type: int32
    endian: big_endian
    factor: 1
```

Supported `register_type` values: `holding`, `input`, `coil`, `discrete_input`.

Supported `type` values: `uint8`, `int8`, `uint16`, `int16`, `uint32`, `int32`, `float32`.

Supported `endian` values: `big_endian`, `little_endian`.

`timeout_ms` is converted to the driver's `request_timeout_seconds` field when a profile is applied. Optional register sections may be omitted.
