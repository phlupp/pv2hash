from dataclasses import dataclass


PROFILE_ORDER = ("off", "eco", "mid", "high")
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


def _next_profile(profile: str) -> str:
    idx = PROFILE_INDEX[_normalize_profile(profile)]
    return PROFILE_ORDER[min(idx + 1, len(PROFILE_ORDER) - 1)]


def _prev_profile(profile: str) -> str:
    idx = PROFILE_INDEX[_normalize_profile(profile)]
    return PROFILE_ORDER[max(idx - 1, 0)]


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
        current_profile = _normalize_profile(current[active[0]])
        next_profile = _next_profile(current_profile)

        if next_profile == current_profile:
            return DistributionPlan(
                profiles=current,
                delta_power_w=0.0,
                changed=False,
                reason="already_at_top",
            )

        target = current.copy()
        delta = 0.0

        for idx in active:
            delta += max(
                0.0,
                miner_delta_power_w=miners[idx].get_profile_power_w(next_profile)
                - miners[idx].get_profile_power_w(current[idx]),
            )
            target[idx] = next_profile

        return DistributionPlan(
            profiles=target,
            delta_power_w=delta,
            changed=target != current,
            reason=f"equal:{current_profile}->{next_profile}",
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
        current_profile = _normalize_profile(current[active[0]])
        prev_profile = _prev_profile(current_profile)

        if prev_profile == current_profile:
            return DistributionPlan(
                profiles=current,
                delta_power_w=0.0,
                changed=False,
                reason="already_at_bottom",
            )

        target = current.copy()
        delta = 0.0

        for idx in active:
            delta += max(
                0.0,
                miners[idx].get_profile_power_w(current[idx])
                - miners[idx].get_profile_power_w(prev_profile),
            )
            target[idx] = prev_profile

        return DistributionPlan(
            profiles=target,
            delta_power_w=delta,
            changed=target != current,
            reason=f"equal:{current_profile}->{prev_profile}",
        )

    if distribution_mode == "cascade":
        for idx in reversed(active):
            current_profile = _normalize_profile(current[idx])
            prev_profile = _prev_profile(current_profile)

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