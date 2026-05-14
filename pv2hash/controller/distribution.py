from dataclasses import dataclass

PROFILE_ORDER = ("off", "p1", "p2", "p3", "p4")
PROFILE_INDEX = {name: idx for idx, name in enumerate(PROFILE_ORDER)}
MIN_EFFECTIVE_POWER_DELTA_W = 1.0


@dataclass
class DistributionPlan:
    profiles: list[str]
    delta_power_w: float
    changed: bool
    reason: str


def _normalize_profile(profile: str | None) -> str:
    if profile in PROFILE_ORDER:
        return profile
    return "off"


def is_profile_higher(left: str | None, right: str | None) -> bool:
    return PROFILE_INDEX[_normalize_profile(left)] > PROFILE_INDEX[_normalize_profile(right)]


def max_profile(left: str | None, right: str | None) -> str:
    normalized_left = _normalize_profile(left)
    normalized_right = _normalize_profile(right)
    if PROFILE_INDEX[normalized_left] >= PROFILE_INDEX[normalized_right]:
        return normalized_left
    return normalized_right


def clamp_profile_to_max(profile: str | None, max_allowed_profile: str | None) -> str:
    normalized_profile = _normalize_profile(profile)
    normalized_max = _normalize_profile(max_allowed_profile)

    if PROFILE_INDEX[normalized_profile] > PROFILE_INDEX[normalized_max]:
        return normalized_max
    return normalized_profile


def clamp_profile_to_min(profile: str | None, min_allowed_profile: str | None) -> str:
    normalized_profile = _normalize_profile(profile)
    normalized_min = _normalize_profile(min_allowed_profile)

    if PROFILE_INDEX[normalized_profile] < PROFILE_INDEX[normalized_min]:
        return normalized_min
    return normalized_profile


def apply_profile_caps(
    profiles: list[str],
    max_profiles: list[str],
) -> list[str]:
    return [
        clamp_profile_to_max(profile, max_profile_name)
        for profile, max_profile_name in zip(profiles, max_profiles)
    ]


def _next_profile(profile: str) -> str:
    idx = PROFILE_INDEX[_normalize_profile(profile)]
    return PROFILE_ORDER[min(idx + 1, len(PROFILE_ORDER) - 1)]


def _prev_profile(profile: str, min_profile: str = "off") -> str:
    normalized = _normalize_profile(profile)
    normalized_min = _normalize_profile(min_profile)

    current_idx = PROFILE_INDEX[normalized]
    min_idx = PROFILE_INDEX[normalized_min]

    if current_idx <= min_idx:
        return normalized

    return PROFILE_ORDER[current_idx - 1]


def _profile_power_w(miner, profile: str | None) -> float:
    try:
        return float(miner.get_profile_power_w(_normalize_profile(profile)))
    except Exception:
        return 0.0


def _next_effective_higher_profile(
    miner,
    current_profile: str | None,
) -> tuple[str, float] | None:
    """Return the next higher profile that actually increases configured power.

    Some installations intentionally configure unused logical steps with the same
    power value, for example p1 == p2 to model a three-step miner. The controller
    should treat those profiles as aliases and skip them instead of getting stuck
    on a zero-watt step.
    """
    current = _normalize_profile(current_profile)
    current_idx = PROFILE_INDEX[current]
    current_power_w = _profile_power_w(miner, current)

    for candidate in PROFILE_ORDER[current_idx + 1 :]:
        candidate_power_w = _profile_power_w(miner, candidate)
        delta = candidate_power_w - current_power_w
        if delta >= MIN_EFFECTIVE_POWER_DELTA_W:
            return candidate, delta

    return None


def _previous_effective_lower_profile(
    miner,
    current_profile: str | None,
    min_profile: str = "off",
) -> tuple[str, float] | None:
    """Return the previous lower profile that actually releases power."""
    current = _normalize_profile(current_profile)
    floor = _normalize_profile(min_profile)
    current_idx = PROFILE_INDEX[current]
    floor_idx = PROFILE_INDEX[floor]

    if current_idx <= floor_idx:
        return None

    current_power_w = _profile_power_w(miner, current)
    for candidate in reversed(PROFILE_ORDER[floor_idx:current_idx]):
        candidate_power_w = _profile_power_w(miner, candidate)
        delta = current_power_w - candidate_power_w
        if delta >= MIN_EFFECTIVE_POWER_DELTA_W:
            return candidate, delta

    return None


def _previous_profile_towards_floor(
    miner,
    current_profile: str | None,
    floor_profile: str | None,
) -> tuple[str, float] | None:
    """Step down toward a floor while skipping zero-release intermediate steps.

    Battery floors are profile targets as well as power limits. If the current
    profile is above the floor and every intermediate step has the same power,
    returning the floor is still useful to keep the runtime state clean. If a
    lower intermediate profile releases real power, return that first to keep the
    existing one-step-at-a-time behaviour.
    """
    current = _normalize_profile(current_profile)
    floor = _normalize_profile(floor_profile)
    current_idx = PROFILE_INDEX[current]
    floor_idx = PROFILE_INDEX[floor]

    if current_idx <= floor_idx:
        return None

    effective_lower = _previous_effective_lower_profile(miner, current, floor)
    if effective_lower is not None:
        return effective_lower

    floor_delta = max(0.0, _profile_power_w(miner, current) - _profile_power_w(miner, floor))
    return floor, floor_delta


def get_current_profiles(miners: list) -> list[str]:
    profiles: list[str] = []

    for miner in miners:
        if not miner.is_active_for_distribution():
            profiles.append("off")
            continue

        profiles.append(_normalize_profile(miner.get_current_profile()))

    return profiles


def _active_indices(miners: list) -> list[int]:
    return [idx for idx, miner in enumerate(miners) if miner.is_active_for_distribution()]


def get_step_up_plan(distribution_mode: str, miners: list) -> DistributionPlan:
    current = get_current_profiles(miners)
    active = _active_indices(miners)

    if not active:
        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="no_active_miners",
        )

    if distribution_mode == "equal":
        target = current.copy()
        delta = 0.0
        changed = False
        skipped_zero_steps = False

        for idx in active:
            current_profile = _normalize_profile(current[idx])
            step = _next_effective_higher_profile(miners[idx], current_profile)

            if step is None:
                continue

            next_profile, step_delta = step
            if next_profile != _next_profile(current_profile):
                skipped_zero_steps = True

            delta += step_delta
            target[idx] = next_profile
            changed = True

        if not changed:
            return DistributionPlan(
                profiles=current,
                delta_power_w=0.0,
                changed=False,
                reason="already_at_top_or_no_higher_power_profile",
            )

        reason = "equal:step_up"
        if skipped_zero_steps:
            reason += ":skip_zero_delta_profiles"

        return DistributionPlan(
            profiles=target,
            delta_power_w=delta,
            changed=True,
            reason=reason,
        )

    if distribution_mode == "cascade":
        skipped_zero_steps = False
        for idx in active:
            current_profile = _normalize_profile(current[idx])
            step = _next_effective_higher_profile(miners[idx], current_profile)

            if step is None:
                continue

            next_profile, delta = step
            if next_profile != _next_profile(current_profile):
                skipped_zero_steps = True

            target = current.copy()
            target[idx] = next_profile
            reason = f"cascade:{idx}:{current_profile}->{next_profile}"
            if skipped_zero_steps:
                reason += ":skip_zero_delta_profiles"

            return DistributionPlan(
                profiles=target,
                delta_power_w=delta,
                changed=True,
                reason=reason,
            )

        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="already_at_top_or_no_higher_power_profile",
        )

    return DistributionPlan(
        profiles=current,
        delta_power_w=0.0,
        changed=False,
        reason="unknown_distribution_mode",
    )


def get_step_down_plan(distribution_mode: str, miners: list) -> DistributionPlan:
    current = get_current_profiles(miners)
    active = _active_indices(miners)

    if not active:
        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="no_active_miners",
        )

    if distribution_mode == "equal":
        target = current.copy()
        delta = 0.0
        changed = False
        skipped_zero_steps = False

        for idx in active:
            current_profile = _normalize_profile(current[idx])
            min_profile = miners[idx].get_min_regulated_profile()
            step = _previous_effective_lower_profile(miners[idx], current_profile, min_profile)

            if step is None:
                continue

            prev_profile, step_delta = step
            if prev_profile != _prev_profile(current_profile, min_profile):
                skipped_zero_steps = True

            delta += step_delta
            target[idx] = prev_profile
            changed = True

        if not changed:
            return DistributionPlan(
                profiles=current,
                delta_power_w=0.0,
                changed=False,
                reason="already_at_bottom_or_no_lower_power_profile",
            )

        reason = "equal:step_down"
        if skipped_zero_steps:
            reason += ":skip_zero_delta_profiles"

        return DistributionPlan(
            profiles=target,
            delta_power_w=delta,
            changed=True,
            reason=reason,
        )

    if distribution_mode == "cascade":
        skipped_zero_steps = False
        for idx in reversed(active):
            current_profile = _normalize_profile(current[idx])
            min_profile = miners[idx].get_min_regulated_profile()
            step = _previous_effective_lower_profile(miners[idx], current_profile, min_profile)

            if step is None:
                continue

            prev_profile, delta = step
            if prev_profile != _prev_profile(current_profile, min_profile):
                skipped_zero_steps = True

            target = current.copy()
            target[idx] = prev_profile
            reason = f"cascade:{idx}:{current_profile}->{prev_profile}"
            if skipped_zero_steps:
                reason += ":skip_zero_delta_profiles"

            return DistributionPlan(
                profiles=target,
                delta_power_w=delta,
                changed=True,
                reason=reason,
            )

        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="already_at_bottom_or_no_lower_power_profile",
        )

    return DistributionPlan(
        profiles=current,
        delta_power_w=0.0,
        changed=False,
        reason="unknown_distribution_mode",
    )


def get_step_down_plan_to_profiles(
    distribution_mode: str,
    miners: list,
    floor_profiles: list[str],
) -> DistributionPlan:
    current = get_current_profiles(miners)
    active = _active_indices(miners)

    if not active:
        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="no_active_miners",
        )

    def step_down_index(idx: int) -> tuple[str, float] | None:
        current_profile = _normalize_profile(current[idx])
        floor_profile = _normalize_profile(
            floor_profiles[idx] if idx < len(floor_profiles) else miners[idx].get_min_regulated_profile()
        )
        return _previous_profile_towards_floor(miners[idx], current_profile, floor_profile)

    if distribution_mode == "equal":
        target = current.copy()
        delta = 0.0
        changed = False
        skipped_zero_steps = False

        for idx in active:
            current_profile = _normalize_profile(current[idx])
            floor_profile = _normalize_profile(
                floor_profiles[idx] if idx < len(floor_profiles) else miners[idx].get_min_regulated_profile()
            )
            step = step_down_index(idx)
            if step is None:
                continue

            prev_profile, step_delta = step
            if prev_profile != _prev_profile(current_profile, floor_profile):
                skipped_zero_steps = True
            target[idx] = prev_profile
            delta += step_delta
            changed = True

        if not changed:
            return DistributionPlan(
                profiles=current,
                delta_power_w=0.0,
                changed=False,
                reason="already_at_battery_floor",
            )

        reason = "equal:battery_step_down"
        if skipped_zero_steps:
            reason += ":skip_zero_delta_profiles"

        return DistributionPlan(
            profiles=target,
            delta_power_w=delta,
            changed=True,
            reason=reason,
        )

    if distribution_mode == "cascade":
        skipped_zero_steps = False
        for idx in reversed(active):
            current_profile = _normalize_profile(current[idx])
            floor_profile = _normalize_profile(
                floor_profiles[idx] if idx < len(floor_profiles) else miners[idx].get_min_regulated_profile()
            )
            step = step_down_index(idx)
            if step is None:
                continue

            prev_profile, delta = step
            if prev_profile != _prev_profile(current_profile, floor_profile):
                skipped_zero_steps = True
            target = current.copy()
            target[idx] = prev_profile
            reason = f"cascade:{idx}:battery_step_down"
            if skipped_zero_steps:
                reason += ":skip_zero_delta_profiles"

            return DistributionPlan(
                profiles=target,
                delta_power_w=delta,
                changed=True,
                reason=reason,
            )

        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="already_at_battery_floor",
        )

    return DistributionPlan(
        profiles=current,
        delta_power_w=0.0,
        changed=False,
        reason="unknown_distribution_mode",
    )
