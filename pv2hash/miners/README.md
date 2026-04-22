# Miner driver architecture (FINAL)

This document defines the **binding data model + GUI contract** for PV2Hash miner drivers.

It is the **single source of truth** for:
- driver implementation
- GUI rendering
- future extensibility

---

# 1. Core Principle

PV2Hash uses a **metadata-driven architecture**:

→ The GUI is NOT hardcoded  
→ The GUI is rendered from driver + core metadata

---

# 2. Final Schema Composition

Final GUI schema is ALWAYS:

    FINAL = CORE_FIELDS + DRIVER_FIELDS

Rules:

- CORE_FIELDS are defined by PV2Hash
- DRIVER_FIELDS are defined by the driver
- Drivers MUST NOT redefine core fields

---

# 3. Field Model (Binding)

Every GUI field MUST follow this structure:

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

# 4. Driver API (Mandatory)

Every driver MUST implement:

```python
def get_config_schema(self) -> list[DriverField]:
    ...
```

This is the ONLY source for GUI field rendering.

---

# 5. Field Categories (Strict Separation)

Fields are logically separated into:

### 5.1 CORE FIELDS (PV2Hash)

Examples:
- enabled
- priority
- floor / p1..p4
- battery_behavior

Rules:
- NOT defined in driver
- ALWAYS injected by PV2Hash

---

### 5.2 DRIVER CONFIG FIELDS

Examples:
- host
- port
- account
- password

---

### 5.3 DEVICE SETTINGS

Examples:
- fan_poweroff_cool
- power_limit

These are persistent miner settings.

---

### 5.4 ACTIONS

Examples:
- reboot
- restart service
- apply settings

---

### 5.5 DETAILS (READ-ONLY)

Examples:
- temperatures
- fan speed
- PSU data
- hashboards

---

# 6. Create Flow (Fixed)

The create flow is globally defined:

1. Select driver
2. Render ONLY fields where:

       create_phase == "basic"

3. User clicks create
4. Miner is created
5. Optional initial probe
6. Miner card opens expanded

---

# 7. Presets vs Defaults

IMPORTANT:

- preset → UI prefilled value
- default → backend fallback

Example:

    port preset = 4433 (API 3.0)

---

# 8. Miner UI Concept

## Collapsed Card

Shows:
- name
- driver
- active
- connection
- runtime state
- priority

## Expanded Card

Renders:

- CORE_FIELDS
- DRIVER_FIELDS
- (later) DEVICE SETTINGS
- (later) ACTIONS
- (later) DETAILS

---

# 9. Simulator Driver

- must implement schema
- minimal fields
- may use fake presets

---

# 10. Migration Strategy

1. introduce schema
2. rebuild GUI
3. migrate simulator
4. migrate braiins
5. migrate whatsminer API 3
6. remove legacy UI

---

# 11. Compatibility Rule

Avoid legacy code.

If needed:

```python
# TODO(driver-ui-migration): remove after migration
```

---

# 12. Anti-Patterns

DO NOT:

- hardcode GUI per driver
- duplicate core fields
- use config as truth if runtime exists
- spam writes every loop

---

# 13. Goal

After this:

- adding a new driver = only backend work
- GUI updates automatically
- system remains scalable
