#!/usr/bin/env python3
"""Safely stage ::/0 WireGuard cryptokey AllowedIPs for btcnode2, live only.

No changes to config files, Linux routing, addresses, firewall, DNS or services.
--check read-only; --apply updates only existing peer in running WireGuard;
--rollback restores the original exact peer AllowedIPs.
"""
import os
import subprocess
import sys

IFACE = "wg-btcnode2"
KEY = "i7RSqgfZi88gZFFzR6BZkR8OOeHFA8hWrjWuUYR3KFw="
OLD = ("10.99.0.1/32", "10.20.0.250/32", "fd8b:6f2e:4a91:99::1/128")
NEW = OLD + ("::/0",)
IP = "/usr/sbin/ip"
WG = "/usr/bin/wg"


def fail(message):
    raise RuntimeError(message)


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def peer_state():
    lines = run(WG, "show", IFACE, "allowed-ips").stdout.splitlines()
    if len(lines) != 1:
        fail("Expected exactly one WireGuard peer")
    fields = lines[0].split()
    if fields[0] != KEY:
        fail("Unexpected gateway peer public key")
    current = set(fields[1:])
    if current not in (set(OLD), set(NEW)):
        fail("Unexpected live AllowedIPs: refusing to change")
    return current


def assert_baseline():
    v4 = run(IP, "-4", "route", "show", "default").stdout
    if "via 192.168.8.1 dev eth0" not in v4:
        fail("IPv4 default route no longer matches baseline")
    addresses = run(IP, "-6", "-o", "address", "show").stdout
    if "2a03:4000:10:317::2020" in addresses:
        fail("Public IPv6 address already assigned; stage is no longer applicable")
    ula = run(IP, "-6", "-o", "address", "show", "dev", IFACE).stdout
    if "fd8b:6f2e:4a91:99::51/128" not in ula:
        fail("WireGuard ULA missing")
    routes = run(IP, "-6", "route", "show", "table", "2020").stdout.splitlines()
    if routes != ["default dev wg-btcnode2 metric 1024 pref medium"]:
        fail("Unexpected or missing table 2020 default route")
    rules = run(IP, "-6", "rule", "show").stdout
    if "120:\tfrom 2a03:4000:10:317::2020 lookup 2020" not in rules:
        fail("Expected exact source IPv6 rule priority 120 missing")
    if run(IP, "-6", "route", "show", "table", "main", "default").stdout.strip():
        fail("Unexpected main-table IPv6 default route")
    for addr in ("10.99.0.1", "10.20.0.250"):
        route = run(IP, "-4", "route", "get", addr).stdout
        if " dev " + IFACE not in route:
            fail("IPv4 management route changed: " + addr)
    for service in ("wg-quick@" + IFACE + ".service", "btcnode2-wg-icmpv6.timer"):
        if run("/usr/bin/systemctl", "is-active", service).stdout.strip() != "active":
            fail("Service inactive: " + service)
    return v4, rules, routes


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply", "--status", "--rollback"):
        print("Usage: sudo python3 stage-btcnode2-wg-ipv6-allowedips.py --check|--apply|--status|--rollback", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("Run on myNode with sudo")
    mode = sys.argv[1]
    original_routes = assert_baseline()
    before = peer_state()
    print("PRECHECK_OK tunnel=" + IFACE + " ipv4_and_src_policy_intact=yes")
    print("LIVE_CRYPTOKEY_IPV6_DEFAULT=" + ("present" if "::/0" in before else "absent"))
    if mode in ("--check", "--status"):
        print("CHECK_OK nothing_changed")
        return 0
    desired = set(OLD) if mode == "--rollback" else set(NEW)
    if before == desired:
        print("ALREADY_IN_TARGET_STATE nothing_changed")
        return 0
    result = run(WG, "set", IFACE, "peer", KEY, "allowed-ips", ",".join(NEW if mode == "--apply" else OLD))
    try:
        if peer_state() != desired:
            fail("WireGuard live peer verification failed")
        after_routes = assert_baseline()
        if after_routes != original_routes:
            fail("Route or policy configuration unexpectedly changed")
    except Exception:
        try:
            run(WG, "set", IFACE, "peer", KEY, "allowed-ips", ",".join(OLD if before == set(OLD) else NEW))
            print("ROLLBACK_OK previous peer AllowedIPs restored", file=sys.stderr)
        except Exception as e:
            print("ROLLBACK_FAILED " + str(e), file=sys.stderr)
        raise
    if mode == "--apply":
        print("APPLY_OK live_peer_accepts_global_ipv6_sources=yes")
    else:
        print("ROLLBACK_OK removed_runtime_cryptokey_default=yes")
    print("PERSISTENCE=no WG_RESTART=no ROUTES_CHANGED=no FIREWALL_CHANGED=no PUBLIC_IPV6_ASSIGNED=no")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("ERROR:", str(error), file=sys.stderr)
        sys.exit(1)
