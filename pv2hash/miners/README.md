# Miner driver architecture

This folder contains miner adapters used by PV2Hash runtime.

## Contract to runtime/controller

The controller decides a target profile (`off`, `p1`, `p2`, `p3`, `p4`).
The runtime then calls `set_profile(profile)` for each active miner **on every loop iteration**.

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

