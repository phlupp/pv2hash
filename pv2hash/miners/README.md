# Miner driver architecture

This folder contains miner adapters used by PV2Hash runtime.

## Contract to runtime/controller

The controller decides a target profile (`off`, `p1`, `p2`, `p3`, `p4`).
The runtime reads status from every miner with `monitor_enabled = true`. The controller only calls `set_profile(profile)` for miners with `control_enabled = true`.

That means a miner driver must be:

- **idempotent**
- **state-aware**
- **safe to call repeatedly**

A driver must never assume that `set_profile()` is only called when the profile changed.
The driver itself must decide whether a real miner write is necessary.

## Reference design

`braiins.py` is the structural reference driver.
New drivers should follow the same pattern unless the vendor API requires a different behavior.

### Braiins-style rules

- If target profile is `off`
  - send stop/pause **only if miner is not already off/paused**
- If target profile is `p1..p4`
  - send start/resume **only if miner is currently off/paused**
  - send target power update **only if the currently effective target differs from the desired one**
- Never rely on a generic loop cooldown as the main safety mechanism
- Use miner runtime state and live readback as the primary source of truth

## WhatsMiner API 2.x notes

WhatsMiner API 2.x differs from Braiins in transport and semantics, but should still follow the same high-level design:

- start/stop must be based on live miner state (`mineroff`)
- percentage/power writes must only happen when the desired target really changed
- live values reported by the miner are the primary truth
- configuration values are only fallbacks or user-entered desired base values

## Source of truth rules

Prefer live miner runtime values over config values whenever possible.

Examples:

- current power limit reported by miner
- current effective percent reported by miner
- current paused/running state

Config should only be used when:

- the miner has no usable live value yet
- the user explicitly sets a desired persistent base value

## Recommended adapter structure

1. `get_status()`
   - read all relevant runtime values from miner
   - update `self.info`
   - cache any runtime fields needed for idempotent writes

2. `set_profile(profile)`
   - convert profile to desired runtime target
   - check current miner state
   - write only if needed

3. verification
   - after writes, verify with a follow-up read when possible
   - prefer actual runtime state fields over assumptions

## Release checklist for a new miner adapter

Before considering a new adapter stable, verify all of the following:

- [ ] `get_status()` returns stable runtime state and does not crash on partial/missing fields
- [ ] `set_profile("off")` is idempotent and does not resend stop endlessly
- [ ] `set_profile("p1")` from off starts the miner reliably
- [ ] repeated `set_profile("p1")` does not spam identical writes
- [ ] step up (`p1 -> p2 -> p3 -> p4`) works
- [ ] step down (`p4 -> p3 -> p2 -> p1`) works
- [ ] switching back to `off` still works after previous power changes
- [ ] live runtime values are preferred over config fallbacks
- [ ] config values are only used when live values are unavailable
- [ ] the adapter remains responsive when the runtime loop calls `set_profile()` repeatedly
- [ ] start/stop logic cannot be blocked by an unrelated power-target mechanism
- [ ] logs are informative but not noisy in normal operation

## Important anti-patterns

Avoid these patterns unless a miner API absolutely forces them:

- global cooldowns as the primary control mechanism
- writing the same target every loop without checking actual state
- using config as truth when a live miner value exists
- coupling stop/start logic to a power-target write path
- broad write-variant brute force in the normal control loop

## Driver metadata for GUI and details

To reduce hard-coded per-driver UI changes, miner drivers should expose metadata that describes how PV2Hash can render configuration, device options, actions, and diagnostic details. The web UI should render these metadata structures generically instead of branching on driver names wherever possible.

### Conceptual separation

Keep the following categories strictly separated:

1. **Config fields**
   - Stored in the PV2Hash miner configuration
   - Used to create and validate a miner instance
   - Examples: host, port, username, password, API variant

2. **Device settings**
   - Persisted on the miner itself
   - Not part of the normal PV2Hash control loop
   - Examples: fan poweroff cool, fast boot, power limit, autotuning defaults

3. **Actions**
   - Immediate one-shot operations
   - Triggered explicitly by the user
   - Examples: reboot miner, apply device setting, restart service, toggle maintenance mode

4. **Details**
   - Read-only information for informational/diagnostic views
   - Examples: firmware version, PSU values, board temperatures, fan speeds, tuner state, error codes

### Recommended optional driver metadata hooks

Drivers may extend the base control interface with metadata methods. These methods should be optional so simple drivers such as the simulator can keep implementations minimal.

```python
@classmethod
def get_config_schema(cls) -> dict:
    ...

@classmethod
def get_device_settings_schema(cls) -> dict:
    ...

@classmethod
def get_actions_schema(cls) -> dict:
    ...

def get_device_settings_values(self) -> dict:
    ...

def get_details(self) -> dict:
    ...

@classmethod
def get_capabilities(cls) -> dict:
    ...
```

The exact Python types can evolve later. For now, the important point is that the returned structures are deterministic, serialisable, and stable enough that the UI can render them without driver-specific template logic.

### Config schema guidance

A config field description should usually contain:

- `name`: stable config key
- `label`: user-facing label
- `type`: e.g. `text`, `password`, `number`, `select`, `checkbox`
- `required`: whether the field is mandatory
- `default`: default value if omitted
- `help`: optional help text
- `section`: logical UI section
- `advanced`: whether to hide under advanced options by default
- `choices`: valid options for select-like fields
- `min` / `max` / `step`: for numeric input when useful
- `placeholder`: optional UI hint

The UI should be able to render a usable miner configuration form solely from this schema.

### Device settings guidance

Device settings are miner-side persistent options. They should not be mixed with normal PV2Hash runtime configuration. A setting description should make clear:

- display name and help text
- value type and choices
- whether it is readable, writable, or both
- whether changing it causes restart/re-tune/reload on the miner
- whether PV2Hash should only display it or also allow modifying it

For example, a WhatsMiner API 3 driver may expose `fan_poweroff_cool` as a device setting, while a Braiins driver may expose autotuning-related options.

### Actions guidance

Actions are explicit user-triggered commands and must be clearly separated from passive settings. Actions should include enough metadata for the UI to label and confirm them safely. Typical metadata may include:

- `name`
- `label`
- `description`
- `confirm_text`
- `dangerous`
- `params_schema` for actions that require user input

Examples:

- restart mining service
- reboot miner
- apply a pending device setting now
- set current miner power limit

### Details guidance

Drivers that can expose detailed information should return read-only structured sections instead of free-form blobs wherever possible. Example shape:

```python
{
    "sections": [
        {
            "id": "overview",
            "title": "Overview",
            "items": [
                {"label": "Firmware", "value": "..."},
                {"label": "Power", "value": "1120 W"},
            ],
        },
        {
            "id": "boards",
            "title": "Hash boards",
            "items": [
                {"label": "Board 1 Temp", "value": "69.2 °C"},
                {"label": "Board 2 Temp", "value": "67.8 °C"},
            ],
        },
    ]
}
```

This should support a future miner details page without tying the web layer to specific driver internals.

Detail items may also use structured list/table data when a driver has multiple records to show, for example recent miner errors. The driver is responsible for adapting vendor-specific payloads into stable UI rows and columns. The web UI only renders the generic structure.

Example table-shaped detail item:

```python
{
    "label": "Recent errors",
    "kind": "table",
    "columns": [
        {"key": "time", "label": "Time"},
        {"key": "code", "label": "Error code"},
        {"key": "reason", "label": "Reason"},
    ],
    "rows": [
        {"time": "2025-03-12 14:52:35", "code": "531", "reason": "Slot1 not found."},
    ],
    "empty": "No errors reported",
}
```

For normal key/value detail entries, drivers can omit `kind`; the UI treats them as simple text values.

### Capabilities guidance

Drivers may optionally publish a capability map so the UI and future services can decide what to show or enable. Example:

```python
{
    "supports_start_stop": True,
    "supports_power_target_watts": False,
    "supports_power_percent": True,
    "supports_device_settings": True,
    "supports_actions": True,
    "supports_details": True,
}
```

Capabilities should describe behaviour, not implementation.

### Driver-specific expectations

#### Simulator

The simulator should still implement the same metadata model, but with a minimal schema. It is useful as a reference implementation because it has very few fields and little special behaviour.

#### Braiins

The Braiins driver is expected to expose richer read-only details and possibly Braiins-specific device settings over time.

#### WhatsMiner API 3

WhatsMiner API 3 is expected to benefit strongly from this model because it has driver-specific device settings and actions that should not leak into unrelated drivers.

#### WhatsMiner API 2

WhatsMiner API 2 should be treated as legacy/deprecated where appropriate. New architecture work should prefer the API 3 path whenever practical.

### Implementation rules

- Keep control logic and metadata separate.
- Drivers should describe UI data; they should not render UI directly.
- Device settings and actions must not silently run as part of the normal PV2Hash control loop unless explicitly documented.
- Detail views must remain read-only.
- Metadata should be stable and predictable so future UI work can stay generic.
---

## Extended data model (binding)

### Final schema composition

Final GUI schema is always:

    FINAL = CORE_FIELDS + DRIVER_FIELDS

Rules:

- CORE_FIELDS are provided by PV2Hash
- DRIVER_FIELDS are provided by the driver
- Drivers MUST NOT redefine core fields

---

### DriverField structure

```python
class DriverField:
    name: str
    label: str
    type: str

    required: bool = False
    preset: Any = None
    default: Any = None

    placeholder: str = ""
    help: str = ""

    create_phase: Literal["basic", "full"] = "full"
    advanced: bool = False

    choices: list[Any] | None = None
```

---

### Preset vs Default

- preset: pre-filled UI value (user editable)
- default: backend fallback if value is missing

Example:
- WhatsMiner API 3 port preset = 4433

---

### Create flow

1. User selects driver
2. UI renders fields where create_phase == "basic"
3. User submits form
4. Miner is created
5. Optional initial probe
6. Full configuration is available in expanded miner card

---

### Core fields (PV2Hash)

Core control flags are intentionally split:

- `monitor_enabled`: PV2Hash creates the runtime adapter and keeps reading status/details. Device settings require this.
- `control_enabled`: the PV controller may include the miner in distribution and call `set_profile()`. If this is true, `monitor_enabled` must also be true.

The UI enforces this dependency immediately, and the server normalizes it before saving. A miner may be monitored without being controlled.


These are injected centrally and must NOT be defined by drivers:

- monitor_enabled (`Verbindung`): build adapter, read status/details, allow device settings
- control_enabled (`In Regelung einbeziehen`): controller may apply profiles; requires monitor_enabled
- priority
- profiles (floor, p1..p4)
- battery_behavior

---

### UI rendering rules

The UI must be fully metadata-driven:

- no driver-name conditionals
- no hardcoded forms per driver
- all forms generated from schema

---

### Simulator role

The simulator driver should act as the minimal reference implementation
for the metadata model.

### Current WhatsMiner API 3 device extensions

The WhatsMiner API 3 driver currently exposes miner-side device settings through the generic driver metadata model. Device settings are treated as miner-side live state, not as PV2Hash configuration. The UI should prefill these fields from driver readback via `get_device_settings_values()` and should not persist them as PV2Hash config values.

Supported writable device settings:

- `device_settings.fan_poweroff_cool` -> `set.fan.poweroff_cool`
- `device_settings.power_limit_w` -> `set.miner.power_limit`

Readback sources:

- `device_settings.fan_poweroff_cool` is read from `get.fan.setting` field `fan-poweroff-cool`
- `fan-zero-speed` is read from `get.fan.setting` and may be shown as read-only detail, but is not exposed as a writable setting.
- `device_settings.power_limit_w` is read from `get.miner.status` summary field `power-limit`
- recent device errors are read from `get.device.info` field `error-code` and exposed as a read-only table detail with the columns time, error code, and reason. This is informational only and does not affect driver status evaluation.

`device_settings.power_limit_w` is intentionally optional in the GUI. An empty value is not sent to the miner. This avoids accidentally sending `0` and triggering a restart when the user only wants to apply unrelated fan settings. If a numeric value is entered, it is validated in the range `0..99999` and sent as a JSON number. The miner may reboot to apply this setting.

Supported explicit actions:

- `system_reboot` -> `set.system.reboot`

Actions are not device settings. They are rendered separately and require an explicit user click, with confirmation for dangerous actions such as reboot.

Firmware note for WhatsMiner API 3 fan zero speed:

On the tested M31S+ H6OS firmware `20250214.16.1.AMS`, `set.fan.zero_speed` returns `code=0` / `msg=ok`, but it does not change `fan-zero-speed`. Instead, it toggles `fan-poweroff-cool`. Because the API acknowledges a command while applying the wrong setting, PV2Hash must not expose `fan_zero_speed` as writable for this driver. The value can still be read and displayed as device detail.

## Braiins OS+ driver notes

The Braiins driver follows the same driver-driven GUI model as the WhatsMiner API 3 reference driver:

- configuration fields are exposed through `get_config_schema()`
- read-only diagnostic sections are exposed through `get_details()`
- explicit one-shot operations are exposed through `get_actions_schema()` / `apply_action()`

The Braiins driver intentionally does not expose normal PV2Hash power targets as device settings. Power target handling remains part of the runtime control path (`set_profile()` -> `SetPowerTarget`) so the controller behavior stays unchanged.

Current Braiins actions:

- `pause_mining` -> `PauseMining`
- `resume_mining` -> `ResumeMining`
- `start_miner` -> `Start`
- `reboot_system` -> `Reboot`

Braiins diagnostic details include overview information, power target constraints, hashrate values, tuner state, status raw data, and a generic table for recent errors. These details are informational only and do not affect runtime-state evaluation.
