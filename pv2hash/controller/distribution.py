from dataclasses import dataclass

PROFILE_ORDER = ("off", "p1", "p2", "p3", "p4")
PROFILE_INDEX = {name: idx for idx, name in enumerate(PROFILE_ORDER)}


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

        for idx in active:
            current_profile = _normalize_profile(current[idx])
            next_profile = _next_profile(current_profile)

            if next_profile == current_profile:
                continue

            delta += max(
                0.0,
                miners[idx].get_profile_power_w(next_profile)
                - miners[idx].get_profile_power_w(current_profile),
            )
            target[idx] = next_profile
            changed = True

        if not changed:
            return DistributionPlan(
                profiles=current,
                delta_power_w=0.0,
                changed=False,
                reason="already_at_top",
            )

        return DistributionPlan(
            profiles=target,
            delta_power_w=delta,
            changed=True,
            reason="equal:step_up",
        )

    if distribution_mode == "cascade":
        for idx in active:
            current_profile = _normalize_profile(current[idx])
            next_profile = _next_profile(current_profile)

            if next_profile == current_profile:
                continue

            target = current.copy()
            target[idx] = next_profile

            delta = max(
                0.0,
                miners[idx].get_profile_power_w(next_profile)
                - miners[idx].get_profile_power_w(current_profile),
            )

            return DistributionPlan(
                profiles=target,
                delta_power_w=delta,
                changed=True,
                reason=f"cascade:{idx}:{current_profile}->{next_profile}",
            )

        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="already_at_top",
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

        for idx in active:
            current_profile = _normalize_profile(current[idx])
            min_profile = miners[idx].get_min_regulated_profile()
            prev_profile = _prev_profile(current_profile, min_profile)

            if prev_profile == current_profile:
                continue

            delta += max(
                0.0,
                miners[idx].get_profile_power_w(current_profile)
                - miners[idx].get_profile_power_w(prev_profile),
            )
            target[idx] = prev_profile
            changed = True

        if not changed:
            return DistributionPlan(
                profiles=current,
                delta_power_w=0.0,
                changed=False,
                reason="already_at_bottom",
            )

        return DistributionPlan(
            profiles=target,
            delta_power_w=delta,
            changed=True,
            reason="equal:step_down",
        )

    if distribution_mode == "cascade":
        for idx in reversed(active):
            current_profile = _normalize_profile(current[idx])
            min_profile = miners[idx].get_min_regulated_profile()
            prev_profile = _prev_profile(current_profile, min_profile)

            if prev_profile == current_profile:
                continue

            target = current.copy()
            target[idx] = prev_profile

            delta = max(
                0.0,
                miners[idx].get_profile_power_w(current_profile)
                - miners[idx].get_profile_power_w(prev_profile),
            )

            return DistributionPlan(
                profiles=target,
                delta_power_w=delta,
                changed=True,
                reason=f"cascade:{idx}:{current_profile}->{prev_profile}",
            )

        return DistributionPlan(
            profiles=current,
            delta_power_w=0.0,
            changed=False,
            reason="already_at_bottom",
        )

    return DistributionPlan(
        profiles=current,
        delta_power_w=0.0,
        changed=False,
        reason="unknown_distribution_mode",
    )
