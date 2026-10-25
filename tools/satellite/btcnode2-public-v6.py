#!/usr/bin/env python3
"""btcnode2 public IPv6 preparation on myNode, independent of myNode-managed configs.

Only allow DATUM TCP/23334 and essential ICMPv6 on wg-btcnode2 for public ::2020.
Source routing table 2020 + peer cryptokey ::/0; never an IPv6 main default.
Own systemd timer ensures the live state after reboot and UFW reload.

Modes: --check, --apply, --ensure, --status, --rollback.
"""
from pathlib import Path
import os
import hashlib
import subprocess
import sys
import tempfile

IP = "/usr/sbin/ip"
WG = "/usr/bin/wg"
IP6T = "/usr/sbin/ip6tables-legacy"
SYSTEMCTL = "/usr/bin/systemctl"
IFACE = "wg-btcnode2"
KEY = "i7RSqgfZi88gZFFzR6BZkR8OOeHFA8hWrjWuUYR3KFw="
PUBLIC = "2a03:4000:10:317::2020"
PUB_CIDR = PUBLIC + "/128"
ULA = "fd8b:6f2e:4a91:99::51/128"
GW_ULA = "fd8b:6f2e:4a91:99::1"
OLD = ("10.99.0.1/32", "10.20.0.250/32", GW_ULA + "/128")
NEW = OLD + ("::/0",)
TABLE = "2020"
PRIORITY = "120"
TARGET = Path("/usr/local/sbin/btcnode2-public-v6.py")
SERVICE = Path("/etc/systemd/system/btcnode2-public-v6.service")
TIMER = Path("/etc/systemd/system/btcnode2-public-v6.timer")
ULA_HELPER = Path("/usr/local/sbin/btcnode2-wg-icmpv6.py")
EXPECTED_ULA_HELPER_SHA = "0a22ce3b0ec217e6816516620f2a876adbb94e26a315b780fd79068d4d88ccac"

# Public-facing rules are tied to the WG interface and exact public /128.
# All non-DATUM TCP ports, incl. SSH, RPC and admin GUIs, remain blocked.
RULES = (
    ("INPUT", ("-i", IFACE, "-d", PUB_CIDR, "-p", "tcp", "--dport", "23334", "-j", "ACCEPT")),
    ("OUTPUT", ("-o", IFACE, "-s", PUB_CIDR, "-p", "tcp", "--sport", "23334", "-j", "ACCEPT")),
    ("INPUT", ("-i", IFACE, "-d", PUB_CIDR, "-p", "ipv6-icmp", "-j", "ACCEPT")),
    ("OUTPUT", ("-o", IFACE, "-s", PUB_CIDR, "-p", "ipv6-icmp", "-j", "ACCEPT")),
)
UNIT_TEXT = """[Unit]
Description=Maintain btcnode2's strictly scoped public DATUM IPv6
After=wg-quick@wg-btcnode2.service firewall.service
ConditionPathExists=/sys/class/net/wg-btcnode2

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/btcnode2-public-v6.py --ensure
"""
TIMER_TEXT = """[Unit]
Description=Reconcile btcnode2's DATUM-only public IPv6 routing and firewall

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s
Unit=btcnode2-public-v6.service

[Install]
WantedBy=timers.target
"""


def fail(message):
    raise RuntimeError(message)


def run(*cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def current_peer():
    rows = run(WG, "show", IFACE, "allowed-ips").stdout.splitlines()
    if len(rows) != 1:
        fail("Expected exactly one WireGuard peer")
    peer = rows[0].split()
    if not peer or peer[0] != KEY:
        fail("Gateway peer key mismatch")
    s = set(peer[1:])
    if s not in (set(OLD), set(NEW)):
        fail("Unexpected WireGuard AllowedIPs")
    return s


def current_table():
    res = run(IP, "-6", "route", "show", "table", TABLE, check=False)
    if res.returncode and "FIB table does not exist" not in res.stderr:
        fail("Cannot inspect table 2020: " + res.stderr[:140])
    rows = [x.strip() for x in res.stdout.splitlines() if x.strip()]
    if not rows:
        return False
    if len(rows) != 1 or rows[0] not in (
        "default dev " + IFACE + " metric 1024 pref medium",
        "default dev " + IFACE + " metric 1024",
        "default dev " + IFACE,
    ):
        fail("Table 2020 contains a foreign route")
    return True


def current_rule():
    rows = run(IP, "-6", "rule", "show").stdout.splitlines()
    matches = [x.strip() for x in rows
               if x.strip().startswith(PRIORITY + ":") or
               "lookup " + TABLE in x]
    if not matches:
        return False
    if len(matches) != 1:
        fail("Conflicting IPv6 source-routing rules")
    p = matches[0].split()
    if not (p[0] == PRIORITY + ":" and "from" in p and
            p[p.index("from") + 1] in (PUBLIC, PUB_CIDR) and
            "lookup" in p and p[p.index("lookup") + 1] == TABLE):
        fail("Policy slot 120 / table 2020 already used for a different rule")
    return True


def public_address():
    out = run(IP, "-6", "-o", "addr", "show").stdout
    matches = [x for x in out.splitlines() if "inet6 " + PUB_CIDR in x]
    if len(matches) > 1:
        fail("Duplicate public IPv6 address assignments")
    if matches and matches[0].split()[1].split("@")[0] != IFACE:
        fail("Public IPv6 already owned by another interface")
    return bool(matches)


def firewall_state():
    state = []
    for chain, args in RULES:
        c = run(IP6T, "-w", "-C", chain, *args, check=False)
        if c.returncode not in (0, 1):
            fail("ip6tables check failed: " + c.stderr[:130])
        state.append(c.returncode == 0)
    return state


def preflight():
    if not Path(IP6T).is_file():
        fail("Expected ip6tables-legacy missing")
    if run(SYSTEMCTL, "is-active", "wg-quick@" + IFACE + ".service").stdout.strip() != "active":
        fail("wg-quick service not active")
    if run(SYSTEMCTL, "is-active", "btcnode2-wg-icmpv6.timer").stdout.strip() != "active":
        fail("Earlier ULA ICMPv6 timer not active")
    if (not ULA_HELPER.is_file() or
            hashlib.sha256(ULA_HELPER.read_bytes()).hexdigest() != EXPECTED_ULA_HELPER_SHA):
        fail("Earlier ICMPv6 timer helper not updated for optional ::/0")
    addr4 = run(IP, "-4", "-o", "addr", "show", "dev", IFACE).stdout
    addr6 = run(IP, "-6", "-o", "addr", "show", "dev", IFACE).stdout
    if "inet 10.99.0.51/32" not in addr4 or "inet6 " + ULA not in addr6:
        fail("WG IPv4 or ULA baseline missing")
    route4 = run(IP, "-4", "route", "show", "default").stdout
    if "via 192.168.8.1 dev eth0" not in route4:
        fail("Unexpected myNode IPv4 default route")
    for target in ("10.99.0.1", "10.20.0.250"):
        if " dev " + IFACE not in run(IP, "-4", "route", "get", target).stdout:
            fail("WG IPv4 management route missing: " + target)
    if run(IP, "-6", "route", "show", "table", "main", "default").stdout.strip():
        fail("Unexpected IPv6 main-table default; no full tunnel allowed")
    if not run(SYSTEMCTL, "is-active", "firewall.service").stdout.strip() == "active":
        fail("myNode firewall service inactive")
    return route4


def get_state():
    return {
        "table": current_table(),
        "rule": current_rule(),
        "address": public_address(),
        "allowed": current_peer(),
        "firewall": firewall_state(),
    }


def show_state(state):
    print("PUBLIC_ADDR=" + ("present" if state["address"] else "absent"))
    print("TABLE_2020=" + ("present" if state["table"] else "absent"))
    print("SOURCE_RULE_120=" + ("present" if state["rule"] else "absent"))
    print("WG_CRYPTOKEY_DEFAULT=" + ("present" if "::/0" in state["allowed"] else "absent"))
    print("DATUM_FIREWALL_RULES=" + str(sum(state["firewall"])) + "/4")


def atomic_install(path, data, mode):
    if path.is_symlink():
        fail("Refusing symlink at " + str(path))
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            fail("Different file exists: " + str(path))
        return
    temp = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                         prefix=".btcnode2-public-v6-", delete=False) as f:
            temp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def ensure():
    # Configure firewall BEFORE making a new public address available.
    st = get_state()
    for (chain, args), found in zip(RULES, st["firewall"]):
        if not found:
            run(IP6T, "-w", "-I", chain, "1", *args)
    if not st["table"]:
        run(IP, "-6", "route", "add", "default", "dev", IFACE, "table", TABLE)
    if not st["rule"]:
        run(IP, "-6", "rule", "add", "priority", PRIORITY,
            "from", PUB_CIDR, "lookup", TABLE)
    if not st["address"]:
        run(IP, "-6", "addr", "add", PUB_CIDR, "dev", IFACE)
    if "::/0" not in st["allowed"]:
        run(WG, "set", IFACE, "peer", KEY, "allowed-ips", ",".join(NEW))
    verified = get_state()
    if not (verified["table"] and verified["rule"] and verified["address"] and
            verified["allowed"] == set(NEW) and all(verified["firewall"])):
        fail("Post-change state inconsistent")
    if "via 192.168.8.1 dev eth0" not in preflight():
        fail("IPv4 default changed unexpectedly")
    print("ENSURE_OK public_ipv6_local=yes peer_crypto_ipv6=yes source_policy=yes")
    print("FIREWALL_ONLY_DATUM_TCP_23334_AND_ICMPV6=yes")
    print("MAIN_IPV6_DEFAULT_ROUTE=no")


def rollback():
    # Remove the timer first so it cannot reapply while removing rules.
    if TIMER.is_file():
        run(SYSTEMCTL, "disable", "--now", TIMER.name, check=False)
    if SERVICE.is_file():
        run(SYSTEMCTL, "stop", SERVICE.name, check=False)
    st = get_state()
    # Close public ingress first.
    for (chain, args), found in zip(RULES, st["firewall"]):
        if found:
            run(IP6T, "-w", "-D", chain, *args)
    if "::/0" in st["allowed"]:
        run(WG, "set", IFACE, "peer", KEY, "allowed-ips", ",".join(OLD))
    if st["address"]:
        run(IP, "-6", "addr", "del", PUB_CIDR, "dev", IFACE)
    if st["rule"]:
        run(IP, "-6", "rule", "del", "priority", PRIORITY,
            "from", PUB_CIDR, "lookup", TABLE)
    if st["table"]:
        run(IP, "-6", "route", "del", "default", "dev", IFACE, "table", TABLE)
    for path, content in (
        (TIMER, TIMER_TEXT.encode()),
        (SERVICE, UNIT_TEXT.encode()),
        (TARGET, Path(__file__).read_bytes()),
    ):
        if path.exists():
            if path.is_symlink() or path.read_bytes() != content:
                fail("Unit/script modified; refusing to remove " + str(path))
            path.unlink()
    run(SYSTEMCTL, "daemon-reload")
    now = get_state()
    if now["address"] or now["table"] or now["rule"] or "::/0" in now["allowed"] or any(now["firewall"]):
        fail("Rollback state incomplete")
    print("ROLLBACK_OK source_policy_public_address_peer_and_firewall_removed=yes")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in (
        "--check", "--status", "--apply", "--ensure", "--rollback"
    ):
        print("Usage: sudo python3 btcnode2-public-v6.py --check|--apply|--ensure|--status|--rollback",
              file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("Run with sudo on myNode")
    mode = sys.argv[1]
    preflight()
    if mode == "--rollback":
        rollback()
        return 0
    current = get_state()
    print("PRECHECK_OK IPv4_management=yes IPv6_main_default=no")
    show_state(current)
    if mode in ("--check", "--status"):
        print("CHECK_OK nothing_changed")
        return 0
    if mode == "--ensure":
        ensure()
        return 0
    # Fresh installation only. An unexpected partial setup is not safe to adopt.
    if current["address"] or "::/0" in current["allowed"] or any(current["firewall"]):
        fail("Existing public setup; do not install over unexpected prior state")
    if current["table"] != current["rule"]:
        fail("Partial policy table / rule present")
    for path, contents, perm in (
        (TARGET, Path(__file__).read_bytes(), 0o755),
        (SERVICE, UNIT_TEXT.encode(), 0o644),
        (TIMER, TIMER_TEXT.encode(), 0o644),
    ):
        atomic_install(path, contents, perm)
    run(SYSTEMCTL, "daemon-reload")
    # Ensure synchronously, so status can be checked before activating timer.
    ensure()
    run(SYSTEMCTL, "enable", "--now", TIMER.name)
    if run(SYSTEMCTL, "is-enabled", TIMER.name).stdout.strip() != "enabled":
        fail("Timer was not enabled")
    print("APPLY_OK persistent_public_ipv6_on_mynode=yes timer_enabled=yes")
    print("MYNODE_CONFIG_UNCHANGED=yes WG_RESTART=no")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:
        print("ERROR:", str(err), file=sys.stderr)
        sys.exit(1)
