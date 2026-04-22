# PV2Hash Miner Driver Interface

This document defines how miner drivers integrate into PV2Hash.

The driver system is designed to be:
- extensible
- self-describing
- UI-driven (GUI renders based on driver metadata)

---

# 1. Core Concept

A miner driver is responsible for:

- communicating with the miner device
- exposing capabilities (start/stop/set power/etc.)
- describing its configuration via metadata

The GUI does NOT hardcode driver-specific fields anymore.
Instead, drivers provide metadata that the GUI renders dynamically.

---

# 2. Separation of Responsibilities

## 2.1 Driver Responsibilities

Drivers provide:

- connection fields (host, port, credentials)
- driver-specific configuration
- device-specific capabilities
- optional actions and details

## 2.2 PV2Hash Core Responsibilities

PV2Hash provides global miner configuration:

- enabled / active
- priority
- power profiles (floor, p1–p4)
- battery_behavior
- global regulation logic

👉 These MUST NOT be implemented inside drivers.

---

# 3. Field Model

Each driver defines configuration fields.

## Field Structure

Example:

```python
{
    "name": "host",
    "label": "Host / IP",
    "type": "text",
    "required": True,
    "preset": "192.168.0.100",
    "placeholder": "192.168.x.x",
    "help": "IP address of the miner",
    "create_phase": "basic",
    "advanced": False
}
```

---

# 4. Create Flow

1. User selects a driver
2. GUI loads fields with create_phase == "basic"
3. Miner is created
4. Miner appears expanded for full configuration

---

# 5. Core + Driver Fields

GUI merges:
- Core fields (PV2Hash)
- Driver fields

---

# 6. UI Concept

Miners are displayed as collapsible cards.

---

# 7. Presets

Preset = UI default value.

---

# 8. Future Extensions

- Device Settings
- Actions
- Details

---

# 9. Design Rules

Drivers must not include core fields.

---

# 10. Goal

Decouple GUI and drivers.
