import asyncio
import json
from datetime import UTC, datetime

from pv2hash.miners.base import MinerAdapter
from pv2hash.models.miner import MinerInfo, MinerProfile, MinerProfiles


class BraiinsMiner(MinerAdapter):
    """
    Erste vorsichtige Braiins-Implementierung:
    - liest Status aktuell noch über BOSminer/CGMiner-kompatiblen API-Call
    - set_profile() ist vorerst noch eine lokale Zielvorgabe

    Die eigentliche gRPC-API und echte Power-Targets folgen im nächsten Schritt.
    """

    def __init__(
        self,
        miner_id: str,
        name: str,
        host: str,
        port: int = 4028,
        priority: int = 100,
        enabled: bool = True,
        serial_number: str | None = None,
        model: str | None = None,
        firmware_version: str | None = None,
        profiles: dict | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.target_profile = "off"

        profile_cfg = profiles or {
            "floor": {"power_w": 0},
            "eco": {"power_w": 1200},
            "mid": {"power_w": 2200},
            "high": {"power_w": 3200},
        }

        miner_profiles = MinerProfiles(
            floor=MinerProfile(power_w=float(profile_cfg["floor"]["power_w"])),
            eco=MinerProfile(power_w=float(profile_cfg["eco"]["power_w"])),
            mid=MinerProfile(power_w=float(profile_cfg["mid"]["power_w"])),
            high=MinerProfile(power_w=float(profile_cfg["high"]["power_w"])),
        )

        self.info = MinerInfo(
            id=miner_id,
            name=name,
            host=host,
            driver="braiins",
            enabled=enabled,
            is_active=False,
            priority=priority,
            serial_number=serial_number,
            model=model or "Unknown",
            firmware_version=firmware_version,
            profile="off",
            power_w=0.0,
            profiles=miner_profiles,
            reachable=False,
            runtime_state="unknown",
            control_mode="power_target",
        )

    async def set_profile(self, profile: str) -> None:
        self.target_profile = profile
        self.info.profile = profile

        if profile == "off":
            self.info.power_w = 0.0
            self.info.runtime_state = "paused"
        else:
            desired_w = self.get_profile_power_w(profile)
            if desired_w <= 0:
                self.info.power_w = 0.0
                self.info.runtime_state = "paused"
            else:
                self.info.power_w = desired_w
                self.info.runtime_state = "running"

        self.info.last_error = None
        self.info.last_seen = datetime.now(UTC)

    async def get_status(self) -> MinerInfo:
        response = await self._bosminer_command("summary")

        if response:
            summary = self._extract_summary(response)
            if summary:
                self.info.is_active = True
                self.info.reachable = True
                self.info.runtime_state = "running"
                self.info.last_error = None

                power = self._pick_float(
                    summary,
                    ["Power", "power", "power_consumption", "Power Limit"],
                )
                firmware = self._pick_str(
                    summary,
                    ["BOSminer", "bosminer", "version", "Version"],
                )

                if power is not None:
                    self.info.power_w = power
                if firmware:
                    self.info.firmware_version = firmware
            else:
                self.info.is_active = False
                self.info.reachable = False
                self.info.runtime_state = "unreachable"
                self.info.power_w = 0.0
                self.info.last_error = "summary missing in response"
        else:
            self.info.is_active = False
            self.info.reachable = False
            self.info.runtime_state = "unreachable"
            self.info.power_w = 0.0
            self.info.last_error = "miner not reachable via legacy summary API"

        self.info.last_seen = datetime.now(UTC)
        return self.info

    async def _bosminer_command(self, command: str) -> dict | None:
        payload = json.dumps({"command": command}).encode()

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=2.0,
            )

            writer.write(payload)
            await writer.drain()

            data = await asyncio.wait_for(reader.read(65535), timeout=2.0)

            writer.close()
            await writer.wait_closed()

            text = data.decode(errors="ignore").strip("\x00").strip()
            if not text:
                return None

            return json.loads(text)
        except Exception:
            return None

    def _extract_summary(self, response: dict) -> dict | None:
        for key in ("SUMMARY", "summary"):
            value = response.get(key)
            if isinstance(value, list) and value:
                if isinstance(value[0], dict):
                    return value[0]
            if isinstance(value, dict):
                return value
        return None

    def _pick_float(self, data: dict, keys: list[str]) -> float | None:
        for key in keys:
            if key in data:
                try:
                    return float(data[key])
                except Exception:
                    continue
        return None

    def _pick_str(self, data: dict, keys: list[str]) -> str | None:
        for key in keys:
            if key in data and data[key] is not None:
                return str(data[key])
        return None
