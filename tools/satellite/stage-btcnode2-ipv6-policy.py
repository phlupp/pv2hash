#!/usr/bin/env python3
"""Stage isolated *live-only* IPv6 source policy routing for btcnode2.

Adds only:
  ip -6 route add default dev wg-btcnode2 table 2020
  ip -6 rule add priority 120 from 2a03:4000:10:317::2020/128 lookup 2020

Deliberately does NOT assign ::2020, extend WG AllowedIPs, modify WireGuard
configuration, install persistence, open firewall or update DNS.

Modes: --check, --apply, --status, --rollback.
"""
import ipaddress
import os
import subprocess
import sys

IFACE = "wg-btcnode2"
PUBLIC = "2a03:4000:10:317::2020/128"
TABLE = "2020"
PRIORITY = "120"
GW_KEY = "i7RSqgfZi88gZFFzR6BZkR8OOeHFA8hWrjWuUYR3KFw="
ULA = "fd8b:6f2e:4a91:99::51/128"
OLD_ALLOWED = {"10.99.0.1/32", "10.20.0.250/32", "fd8b:6f2e:4a91:99::1/128"}
IP = "/usr/sbin/ip"
WG = "/usr/bin/wg"


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def fail(message):
    raise RuntimeError(message)


def rule_state():
    s = run(IP, "-6", "rule", "show").stdout
    target = []
    for line in s.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0].rstrip(":") == PRIORITY or ("lookup " + TABLE) in line:
            target.append(line.strip())
    if not target:
        return False
    if len(target) != 1:
        fail("Unexpected existing IPv6 policy rules at priority 120 or table 2020")
    line = target[0]
    # iproute may print 'lookup 2020' or 'lookup 2020' with arbitrary spacing.
    if not (line.startswith(PRIORITY + ":") and
            any("from " + value in line for value in (PUBLIC, PUBLIC[:-4])) and
            "lookup " + TABLE in line):
        fail("Priority 120 or table 2020 already belongs to a different rule: " + line)
    return True


def table_state():
    result = run(IP, "-6", "route", "show", "table", TABLE, check=False)
    if result.returncode and "FIB table does not exist" not in result.stderr:
        fail("Cannot inspect routing table 2020: " + result.stderr[:170])
    entries = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not entries:
        return False
    if entries != ["default dev " + IFACE + " metric 1024 pref medium"] and (
        len(entries) != 1 or
        not entries[0].startswith("default dev " + IFACE + " ") or
        any(x in entries[0] for x in (" via ", " blackhole ", " unreachable "))
    ):
        fail("Routing table 2020 is already used by a different route")
    return True


def check_baseline():
    a4 = run(IP, "-4", "-o", "addr", "show", "dev", IFACE).stdout
    a6 = run(IP, "-6", "-o", "addr", "show", "dev", IFACE).stdout
    if "inet 10.99.0.51/32" not in a4:
        fail("IPv4 WireGuard address missing")
    if "inet6 " + ULA not in a6:
        fail("IPv6 WG ULA address missing")
    if PUBLIC.split("/")[0] in a6 or PUBLIC.split("/")[0] in run(IP, "-6", "-o", "addr", "show").stdout:
        fail("Public IPv6 already assigned: defer to live-public-address stage")
    peer_rows = run(WG, "show", IFACE, "allowed-ips").stdout.splitlines()
    if len(peer_rows) != 1:
        fail("Unexpected WireGuard peer count")
    peer = peer_rows[0].split()
    if peer[0] != GW_KEY or set(peer[1:]) != OLD_ALLOWED:
        fail("Expected current gateway peer and three current AllowedIPs only")
    default4 = run(IP, "-4", "route", "show", "default").stdout
    if "via 192.168.8.1 dev eth0" not in default4:
        fail("Unexpected IPv4 default route")
    for addr in ("10.99.0.1", "10.20.0.250"):
        route = run(IP, "-4", "route", "get", addr).stdout
        if " dev " + IFACE not in route:
            fail("Management IPv4 route not via WG: " + addr)
    for service in ("wg-quick@" + IFACE + ".service", "btcnode2-wg-icmpv6.timer"):
        if run("/usr/bin/systemctl", "is-active", service).stdout.strip() != "active":
            fail("Required service inactive: " + service)
    print("PRECHECK_OK active_wireguard=yes existing_ipv4_routes=yes")
    print("PUBLIC_IPV6_ASSIGNED=no WG_ALLOWEDIPS_UNCHANGED=yes")
    return default4


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply", "--status", "--rollback"):
        print("Usage: sudo python3 stage-btcnode2-ipv6-policy.py --check|--apply|--status|--rollback",
              file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("Must run as root on myNode")
    operation = sys.argv[1]
    default_before = check_baseline()
    route_present = table_state()
    rule_present = rule_state()
    print("POLICY_TABLE_DEFAULT=" + ("present" if route_present else "absent"))
    print("SOURCE_POLICY_RULE=" + ("present" if rule_present else "absent"))
    if operation in ("--check", "--status"):
        if route_present != rule_present:
            fail("Partial IPv6 policy configuration already present; inspect manually")
        print("CHECK_OK nothing_changed")
        return 0
    if operation == "--rollback":
        if rule_present:
            run(IP, "-6", "rule", "del", "priority", PRIORITY, "from", PUBLIC,
                "lookup", TABLE)
        if route_present:
            run(IP, "-6", "route", "del", "default", "dev", IFACE, "table", TABLE)
        if table_state() or rule_state():
            fail("Rollback verification failed")
        print("ROLLBACK_OK staged_policy_removed=yes")
        return 0
    if route_present and rule_present:
        print("ALREADY_APPLIED no_changes")
        return 0
    if route_present != rule_present:
        fail("Partial IPv6 policy state; refusing silent repair")
    added_route = added_rule = False
    try:
        run(IP, "-6", "route", "add", "default", "dev", IFACE, "table", TABLE)
        added_route = True
        run(IP, "-6", "rule", "add", "priority", PRIORITY, "from", PUBLIC, "lookup", TABLE)
        added_rule = True
        if not table_state() or not rule_state():
            fail("New policy not verified")
        if run(IP, "-4", "route", "show", "default").stdout != default_before:
            fail("IPv4 default route changed unexpectedly")
        print("APPLY_OK only_source_2020_policy=yes ipv4_default_preserved=yes")
        print("PUBLIC_IPV6_ASSIGNED=no FIREWALL_CHANGED=no WG_CHANGED=no")
        print("PERSISTENCE=no ; deliberate staged live-only preparation")
        return 0
    except Exception:
        try:
            if added_rule:
                run(IP, "-6", "rule", "del", "priority", PRIORITY, "from", PUBLIC, "lookup", TABLE)
            if added_route:
                run(IP, "-6", "route", "del", "default", "dev", IFACE, "table", TABLE)
            print("ROLLBACK_OK_AFTER_FAILED_APPLY", file=sys.stderr)
        except Exception as exc:
            print("ROLLBACK_FAILED " + str(exc), file=sys.stderr)
        raise


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
