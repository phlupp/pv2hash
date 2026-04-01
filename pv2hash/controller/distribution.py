def apply_distribution(
    distribution_mode: str,
    target_profile: str,
    miners: list,
) -> list[str]:
    count = len(miners)

    if count == 0:
        return []

    if distribution_mode == "equal":
        return [target_profile] * count

    if distribution_mode == "cascade":
        if target_profile == "off":
            return ["off"] * count

        if target_profile == "eco":
            result = ["off"] * count
            result[0] = "eco"
            return result

        if target_profile == "mid":
            result = ["off"] * count
            result[0] = "mid"
            return result

        if target_profile == "high":
            if count == 1:
                return ["high"]
            if count == 2:
                return ["high", "eco"]
            result = ["off"] * count
            result[0] = "high"
            result[1] = "eco"
            return result

    return [target_profile] * count
