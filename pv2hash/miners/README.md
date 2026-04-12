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
