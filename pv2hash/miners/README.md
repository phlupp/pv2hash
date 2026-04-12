# Miner driver architecture (`pv2hash/miners`)

This document describes the expected structure for miner adapters in PV2Hash.

It exists so that new drivers follow the **same stable pattern** as the working Braiins driver and only deviate where a miner API really forces different behavior.

## Goals

A miner driver should:

- present a **simple, stable interface** to the controller
- be **idempotent**: calling `set_profile()` repeatedly with the same target must not spam the miner with unnecessary writes
- keep miner-specific API quirks **inside the adapter**
- expose runtime state through `MinerInfo`
- make the controller independent of vendor-specific protocol details

---

## Interface to the controller/runtime

All miner adapters inherit from `MinerAdapter` in `base.py` and must implement:

- `async def set_profile(self, profile: str) -> None`
- `async def get_status(self) -> MinerInfo`

The runtime/controller works with **profiles**, not vendor-specific commands.

### Important controller behavior

The control loop may call `set_profile()` repeatedly even if the target profile did not change.

This means:

- the driver must **not assume** that `set_profile()` is only called on profile transitions
- the driver must decide internally whether a real write is needed
- the driver should behave like the Braiins driver: **write only if needed**

The controller's `min_switch_interval` only limits **profile decision changes**. It does **not** guarantee that `set_profile()` is called only once.

So the adapter must be safe for repeated calls.

---

## Recommended driver design

### 1. Keep `set_profile()` small and robust

Recommended pattern:

1. store target profile in `self.info.profile`
2. derive desired power from the profile
3. call a synchronous helper with `asyncio.to_thread(...)`
4. catch write exceptions and store them in `self.info.last_error`
5. never raise write errors into the control loop unless absolutely necessary

Typical pattern:

```python
async def set_profile(self, profile: str) -> None:
    self.info.profile = profile
    desired_w = 0.0 if profile == "off" else self.get_profile_power_w(profile)

    try:
        await asyncio.to_thread(self._apply_profile_sync, profile, desired_w)
        self.info.last_error = None
    except Exception as exc:
        self.info.last_error = f"write failed: {exc}"
```

### 2. Put protocol logic into sync helpers

Recommended split:

- `_apply_profile_sync(...)`
- `_fetch_bundle_sync()`
- `_apply_bundle(bundle)`
- vendor-specific `_write_*()` / `_read_*()` helpers

This keeps async code clean and makes debugging easier.

### 3. Make write behavior idempotent

This is the most important rule.

A driver should only send a write when the miner is **not already in the desired state**.

Examples:

- send `power_off` only if the miner is currently on
- send `power_on` only if the miner is currently off
- send a power target only if the current target differs from the desired one
- avoid cool-down based logic as the primary mechanism when a proper state comparison is possible

The Braiins driver is the reference example here.

---

## Reference behavior: Braiins driver

The Braiins driver shows the intended architecture.

### Off/on handling

- profile `off` -> `PauseMining`, but only if runtime state is not already paused/stopped
- non-off profile -> `ResumeMining` / `Start` only if runtime state requires it

### Power writes

- `SetPowerTarget` is only sent if `_needs_power_target_update(desired_w)` says it is necessary

This is the target style for other drivers too:

- **read actual/current state**
- compare against desired state
- **write only if required**

---

## Reference behavior: WhatsMiner API 2.x

WhatsMiner API 2.x is different from Braiins and therefore needs some adapter-specific handling.

### State detection

The relevant runtime state field is:

- `status["Msg"]["mineroff"]`

Not all useful values are on top level.

### Start/stop

- `power_on`
- `power_off`

The working start/stop path should remain narrow and verified.

### Power control

API 2.x does not behave like Braiins OS.

Current intended model:

- read the miner's live `power_limit_set`
- use that as the main reference for calculations
- keep PV2Hash UI in **watts**
- convert desired watts to percent internally for API-2.x-specific control if needed

### Important design rule

For WhatsMiner too, the driver should eventually be made idempotent in the same style as Braiins:

- only send `power_on` if `mineroff == true`
- only send `power_off` if `mineroff == false`
- only send percent/target writes if the requested target differs from the current effective state

---

## `MinerInfo` responsibilities

Each adapter must keep `self.info` updated.

Important fields include:

- `id`
- `name`
- `host`
- `driver`
- `enabled`
- `is_active`
- `priority`
- `profile`
- `power_w`
- `current_hashrate_ghs`
- `runtime_state`
- `reachable`
- `last_seen`
- `last_error`

Runtime state should reflect the miner as accurately as possible, for example:

- `running`
- `paused`
- `stopped`
- `unknown`
- `unreachable`

---

## Profile semantics

Common profile semantics in PV2Hash:

- `off` -> miner should stop/pause mining
- `p1`, `p2`, `p3`, `p4` -> miner should run at the configured level

Driver code should not reinterpret these semantics arbitrarily.

If a vendor API needs different underlying commands, the mapping belongs inside the driver.

---

## Error handling rules

A driver should:

- log write/read failures clearly
- keep `last_error` useful for the UI
- avoid crashing the control loop
- avoid unhandled background task exceptions

If background tasks are used, they must be fully wrapped in `try/except`.

---

## Verification rules

Whenever possible, writes should be verified by reading back a real state signal.

Examples:

- off/on verified by paused/running or `mineroff`
- target change verified by actual target/limit field

A write should not be considered successful only because the transport returned some encrypted/opaque acknowledgment.

---

## When cooldowns are acceptable

Cooldowns are allowed only as a **secondary safety measure**, not as the main control concept.

Use cooldowns when:

- the miner API requires time to settle
- repeated writes would cause restarts or instability
- there is no reliable current-target field yet

Do **not** use cooldowns as a replacement for proper state comparison if the current state can be read.

Preferred order:

1. idempotent state comparison
2. write only if needed
3. optional small cooldown as extra protection

---

## Checklist for new miner drivers

Before adding a new driver, check:

- Does it implement `set_profile()` and `get_status()` cleanly?
- Does it keep all vendor-specific protocol logic inside the adapter?
- Does it read current state before writing?
- Does it avoid repeated writes when nothing changed?
- Does `off` work reliably?
- Does `on` work reliably?
- Is target/power writing verified by a follow-up read if possible?
- Does the UI get useful `last_error` information?
- Does the driver avoid blocking the control loop for long periods?
- Is the driver aligned with the Braiins structure unless the API forces something else?

---

## Recommended implementation sequence for future drivers

For a new miner family, use this order:

1. **Read-only support**
   - status
   - model/firmware
   - hashrate
   - power
   - runtime state

2. **Reliable off/on**
   - verify with follow-up reads

3. **Stable target control**
   - direct watt target if available
   - otherwise safe internal conversion (for example watts -> percent)

4. **Idempotence cleanup**
   - write only when needed

5. **UI integration**
   - only expose fields the driver really needs

This prevents unstable mixed states during development.

---

## Final rule

**Braiins is the structural reference.**

New drivers should copy the same general design:

- same controller interface
- same profile semantics
- same idempotent philosophy
- vendor-specific differences only where technically required

If a miner requires different protocol details, those belong in the adapter — not in the controller.
