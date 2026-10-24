#!/usr/bin/env python3
"""Keep only btcnode2 <-> gw WireGuard-ULA ICMPv6 exceptions after myNode/UFW reloads.

Does not configure public IPv6, modify UFW, myNode services, Bitcoin, DATUM or WG.
Modes: --check (read-only), --apply (install timer), --ensure (idempotent),
       --status (read-only), --rollback (uninstall own timer and rules).
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

IFACE = "wg-btcnode2"
GW_KEY = "i7RSqgfZi88gZFFzR6BZkR8OOeHFA8hWrjWuUYR3KFw="
GW_IP = "fd8b:6f2e:4a91:99::1"
MY_IP = "fd8b:6f2e:4a91:99::51"
IP6T = "/usr/sbin/ip6tables-legacy"
TARGET = Path("/usr/local/sbin/btcnode2-wg-icmpv6.py")
SVC = Path("/etc/systemd/system/btcnode2-wg-icmpv6.service")
TMR = Path("/etc/systemd/system/btcnode2-wg-icmpv6.timer")
RULES = (
    ("INPUT", ("-i", IFACE, "-s", GW_IP + "/128", "-d", MY_IP + "/128",
               "-p", "ipv6-icmp", "-j", "ACCEPT")),
    ("OUTPUT", ("-o", IFACE, "-s", MY_IP + "/128", "-d", GW_IP + "/128",
                "-p", "ipv6-icmp", "-j", "ACCEPT")),
)
SERVICE = """[Unit]
Description=Restore narrowly scoped btcnode2 WireGuard ULA ICMPv6 allowances
After=wg-quick@wg-btcnode2.service firewall.service
ConditionPathExists=/sys/class/net/wg-btcnode2

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/btcnode2-wg-icmpv6.py --ensure
"""
TIMER = """[Unit]
Description=Maintain btcnode2's narrow IPv6 allowances after myNode/UFW reloads

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s
Unit=btcnode2-wg-icmpv6.service

[Install]
WantedBy=timers.target
"""


def fail(message):
    raise RuntimeError(message)


def run(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def preflight():
    if not Path(IP6T).is_file():
        fail("ip6tables-legacy not installed")
    v6addr = run("/usr/sbin/ip", "-6", "-o", "addr", "show", "dev", IFACE).stdout
    if ("inet6 " + MY_IP + "/128") not in v6addr:
        fail("Expected WireGuard ULA address missing")
    v4addr = run("/usr/sbin/ip", "-4", "-o", "addr", "show", "dev", IFACE).stdout
    if "inet 10.99.0.51/32" not in v4addr:
        fail("Expected original WireGuard IPv4 address missing")
    wg = run("/usr/bin/wg", "show", IFACE, "allowed-ips").stdout.splitlines()
    if len(wg) != 1:
        fail("Expected exactly one WireGuard peer")
    fields = wg[0].split()
    if not fields or fields[0] != GW_KEY:
        fail("Unexpected WireGuard peer key")
    expected = {"10.99.0.1/32", "10.20.0.250/32", GW_IP + "/128"}
    if set(fields[1:]) != expected:
        fail("Unexpected AllowedIPs on myNode, refusing firewall adjustments")
    print("PRECHECK_OK mynode_wg_ipv4_preserved=yes peer_ula_match=yes")


def rule_exists(chain, args):
    result = run(IP6T, "-w", "-C", chain, *args, check=False)
    if result.returncode not in (0, 1):
        fail("Unexpected ip6tables error on " + chain + " " + result.stderr[:180])
    return result.returncode == 0


def get_rule_state():
    return tuple(rule_exists(chain, args) for chain, args in RULES)


def install_file(path, contents, permissions):
    if path.is_symlink():
        fail("Refusing symlink: " + str(path))
    if path.exists():
        if not path.is_file() or path.read_bytes() != contents:
            fail("Existing different file; refusing overwrite: " + str(path))
        return "unchanged"
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".btcnode2-wg6-",
                                         mode="wb", delete=False) as out:
            temp = Path(out.name)
            out.write(contents)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, permissions)
        os.replace(temp, path)
        temp = None
        return "installed"
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def systemd_status():
    result = run("/usr/bin/systemctl", "is-enabled", TMR.name, check=False)
    return result.stdout.strip() or "not-installed"


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in (
        "--check", "--apply", "--status", "--ensure", "--rollback"
    ):
        print("Usage: sudo python3 btcnode2-wg-icmpv6.py --check|--apply|--status|--ensure|--rollback",
              file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("Run with sudo on myNode")
    mode = sys.argv[1]
    if mode == "--rollback":
        if TMR.exists():
            run("/usr/bin/systemctl", "disable", "--now", TMR.name, check=False)
        if SVC.exists():
            run("/usr/bin/systemctl", "stop", SVC.name, check=False)
        # Remove files only if they have exactly our expected content.
        for p, expected in ((TMR, TIMER.encode()), (SVC, SERVICE.encode())):
            if p.exists():
                if p.read_bytes() != expected:
                    fail("Service/timer modified; refusing to remove " + str(p))
                p.unlink()
        if TARGET.exists():
            if TARGET.read_bytes() != Path(__file__).read_bytes():
                fail("Deployed script changed; refusing to remove")
            TARGET.unlink()
        run("/usr/bin/systemctl", "daemon-reload")
        for chain, args in RULES:
            if rule_exists(chain, args):
                run(IP6T, "-w", "-D", chain, *args)
        print("ROLLBACK_OK own units and exact ICMPv6 rules removed")
        return 0
    preflight()
    current = get_rule_state()
    print("RULE_INPUT_PRESENT=" + ("yes" if current[0] else "no"))
    print("RULE_OUTPUT_PRESENT=" + ("yes" if current[1] else "no"))
    if mode in ("--check", "--status"):
        print("TIMER=" + systemd_status())
        print("CHECK_OK nothing_changed")
        return 0
    if mode == "--ensure":
        if not current[0]:
            run(IP6T, "-w", "-I", RULES[0][0], "1", *RULES[0][1])
            print("RESTORED_INPUT=yes")
        if not current[1]:
            run(IP6T, "-w", "-I", RULES[1][0], "1", *RULES[1][1])
            print("RESTORED_OUTPUT=yes")
        if get_rule_state() != (True, True):
            fail("Exact-rule verification failed")
        print("ENSURE_OK only_wg_ula_icmpv6_allowed=yes")
        return 0
    if mode == "--apply":
        if current != (True, True):
            fail("First establish and test both manual ICMPv6 rules; refusing install")
        installed_script = Path(__file__).read_bytes()
        for path, data, permission in (
            (TARGET, installed_script, 0o755),
            (SVC, SERVICE.encode(), 0o644),
            (TMR, TIMER.encode(), 0o644)
        ):
            print(str(path) + ": " + install_file(path, data, permission))
        run("/usr/bin/systemctl", "daemon-reload")
        run("/usr/bin/systemctl", "enable", "--now", TMR.name)
        run("/usr/bin/systemctl", "start", SVC.name)
        if systemd_status() != "enabled" or get_rule_state() != (True, True):
            fail("Post-install verification failed")
        print("APPLY_OK timer_enabled=yes rules_verified=yes")
        print("MYNODE_FIREWALL_SCRIPT_UNCHANGED=yes WG_RESTART=no")
        return 0
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
