import json
import subprocess
from typing import Any


def get_local_ip_addresses() -> list[dict[str, Any]]:
    """
    Liefert aktive lokale IP-Adressen mit Interface-Namen.
    Zukunftssicher für IPv4 und IPv6.

    Rückgabeformat:
    [
        {
            "ifname": "ens18",
            "family": "ipv4",
            "address": "192.168.10.99",
            "label": "ens18 — 192.168.10.99 (IPv4)"
        },
        ...
    ]
    """
    entries: list[dict[str, Any]] = [
        {
            "ifname": "*",
            "family": "ipv4",
            "address": "0.0.0.0",
            "label": "Automatisch — 0.0.0.0 (IPv4)",
        }
    ]

    try:
        result = subprocess.run(
            ["ip", "-j", "addr", "show", "up"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)

        for iface in data:
            ifname = iface.get("ifname", "?")
            addr_info = iface.get("addr_info", [])

            for addr in addr_info:
                family = addr.get("family")
                local = addr.get("local")
                scope = addr.get("scope", "")

                if not local:
                    continue

                if family == "inet":
                    entries.append(
                        {
                            "ifname": ifname,
                            "family": "ipv4",
                            "address": local,
                            "label": f"{ifname} — {local} (IPv4)",
                        }
                    )

                elif family == "inet6":
                    # Link-local IPv6 kann später nützlich sein,
                    # global natürlich ebenso.
                    scope_suffix = f", {scope}" if scope else ""
                    entries.append(
                        {
                            "ifname": ifname,
                            "family": "ipv6",
                            "address": local,
                            "label": f"{ifname} — {local} (IPv6{scope_suffix})",
                        }
                    )

    except Exception:
        # Fallback: wenigstens 0.0.0.0 anbieten
        pass

    # Duplikate entfernen, Reihenfolge stabil halten
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []

    for item in entries:
        key = (item["ifname"], item["family"], item["address"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped


def get_local_ipv4_addresses() -> list[dict[str, Any]]:
    """
    Für aktuell IPv4-only Quellen wie SMA Multicast.
    Nutzt die allgemeine Funktion, filtert aber auf IPv4.
    """
    return [item for item in get_local_ip_addresses() if item["family"] == "ipv4"]