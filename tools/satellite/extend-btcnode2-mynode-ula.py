#!/usr/bin/env python3
"""One-time myNode WireGuard ULA extension, no restart, no public-IPv6 exposure."""
from pathlib import Path
import datetime as dt
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import tempfile

IFACE = "wg-btcnode2"
ROOT = Path("/etc/wireguard")
CONF = ROOT / "wg-btcnode2.conf"
BACKUP_DIR = ROOT / "btcnode2-backups"
EXPECTED_MARKER = "# Managed by btcnode2 bootstrap v1"
GW_KEY = "i7RSqgfZi88gZFFzR6BZkR8OOeHFA8hWrjWuUYR3KFw="
OLD_ADDR = ("10.99.0.51/32",)
NEW_ADDR = ("10.99.0.51/32", "fd8b:6f2e:4a91:99::51/128")
OLD_ALLOWED = ("10.99.0.1/32", "10.20.0.250/32")
NEW_ALLOWED = OLD_ALLOWED + ("fd8b:6f2e:4a91:99::1/128",)
CLIENT_ULA = NEW_ADDR[1]
GW_ULA = NEW_ALLOWED[-1]


def fail(message):
    raise RuntimeError(message)


def cmd(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def entries(contents):
    if not contents.startswith(EXPECTED_MARKER + "\n"):
        fail("File is not the expected bootstrap-managed WireGuard configuration")
    lines = contents.splitlines(keepends=True)
    section = ""
    fields = {}
    counts = {"Interface": 0, "Peer": 0}
    for number, line in enumerate(lines):
        if line.lstrip().startswith("#") or not line.strip():
            continue
        m = re.fullmatch(r"\s*\[\s*([A-Za-z]+)\s*\]\s*", line)
        if m:
            section = m.group(1)
            if section not in counts:
                fail("Unexpected WireGuard section")
            counts[section] += 1
            continue
        m = re.match(r"\s*([A-Za-z]+)\s*=\s*([^#;\r\n]*)", line)
        if not m or not section:
            fail("Unexpected or unparseable configuration line")
        name, value = m.group(1).lower(), m.group(2).strip()
        key = (section, name)
        if key in fields:
            fail("Duplicate configuration option: " + section + "/" + name)
        fields[key] = (value, number)
    if counts != {"Interface": 1, "Peer": 1}:
        fail("Expected exactly one Interface and one Peer")
    mandatory = (("Interface", "address"), ("Interface", "mtu"), ("Interface", "privatekey"),
                 ("Peer", "publickey"), ("Peer", "endpoint"),
                 ("Peer", "allowedips"), ("Peer", "persistentkeepalive"))
    if not all(k in fields for k in mandatory):
        fail("Unexpected missing WireGuard bootstrap fields")
    if fields[("Peer", "publickey")][0] != GW_KEY:
        fail("WireGuard Gateway public key does not match expected key")
    if fields[("Peer", "endpoint")][0] != "gw.phlupp.net:51820":
        fail("Unexpected Gateway endpoint; review config manually")
    if fields[("Interface", "mtu")][0] != "1360":
        fail("Unexpected MTU; review config manually")

    def parse_cidrs(raw):
        return tuple(str(ipaddress.ip_network(v.strip(), strict=False))
                     for v in raw.split(",") if v.strip())

    configured_address = parse_cidrs(fields[("Interface", "address")][0])
    configured_allowed = parse_cidrs(fields[("Peer", "allowedips")][0])
    if configured_address not in (OLD_ADDR, NEW_ADDR):
        fail("Unexpected WireGuard Interface Address values")
    if configured_allowed not in (OLD_ALLOWED, NEW_ALLOWED):
        fail("Unexpected WireGuard AllowedIPs values")
    return lines, fields, configured_address, configured_allowed


def runtime_allowed():
    rows = cmd("wg", "show", IFACE, "allowed-ips").splitlines()
    if len(rows) != 1:
        fail("Expected a single runtime WG peer")
    fields = rows[0].split()
    if fields[0] != GW_KEY:
        fail("Unexpected runtime peer public key")
    result = tuple(str(ipaddress.ip_network(v.strip(), strict=False))
                   for part in fields[1:] for v in part.split(",") if v.strip())
    if result not in (OLD_ALLOWED, NEW_ALLOWED):
        fail("Unexpected runtime AllowedIPs")
    return result


def runtime_address():
    out = cmd("ip", "-o", "-6", "address", "show")
    matches = []
    for row in out.splitlines():
        pieces = row.split()
        if "inet6" in pieces and CLIENT_ULA in pieces:
            matches.append(pieces[1].rstrip(":"))
    if matches and matches != [IFACE]:
        fail("ULA is already in use on unexpected interface")
    return bool(matches)


def route_state():
    out = cmd("ip", "-6", "route", "show", "exact", GW_ULA)
    if not out.strip():
        return False
    if any(" dev " + IFACE not in (" " + line + " ") for line in out.splitlines()):
        fail("Conflicting route to gateway ULA")
    return True


def show_current():
    config = CONF.read_text(encoding="utf-8")
    lines, fields, address, allowed = entries(config)
    current_allowed = runtime_allowed()
    has_address = runtime_address()
    has_route = route_state()
    print("MYNODE_PREFLIGHT_OK iface=" + IFACE + " service=" +
          cmd("systemctl", "is-active", "wg-quick@" + IFACE + ".service").strip())
    print("CONFIG_ADDRESS=" + ",".join(address))
    print("CONFIG_ALLOWED_IPS=" + ",".join(allowed))
    print("LIVE_ALLOWED_IPS=" + ",".join(current_allowed))
    print("LIVE_ULA_ADDRESS=" + ("yes" if has_address else "no"))
    print("LIVE_ULA_ROUTE=" + ("yes" if has_route else "no"))
    return config, lines, fields, address, allowed, current_allowed, has_address, has_route


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply", "--status"):
        print("Usage: sudo python3 extend-btcnode2-mynode-ula.py --check|--apply|--status", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("Run as root through sudo")
    if CONF.is_symlink() or not CONF.is_file() or CONF.stat().st_mode & 0o077:
        fail("Refusing unusual or non-private WireGuard configuration")
    if cmd("systemctl", "is-active", "wg-quick@" + IFACE + ".service").strip() != "active":
        fail("WireGuard interface service not active")
    config, lines, fields, addr, allowed, running, has_addr, has_route = show_current()
    mode = sys.argv[1]
    if mode == "--status":
        return 0
    if mode == "--check":
        if (addr, allowed, running, has_addr, has_route) not in (
            (OLD_ADDR, OLD_ALLOWED, OLD_ALLOWED, False, False),
            (NEW_ADDR, NEW_ALLOWED, NEW_ALLOWED, True, True),
        ):
            fail("Config/runtime in unexpected partial state")
        print("CHECK_OK nothing_changed")
        return 0
    if (addr, allowed, running, has_addr, has_route) == (
        NEW_ADDR, NEW_ALLOWED, NEW_ALLOWED, True, True):
        print("ALREADY_APPLIED nothing_changed")
        return 0
    if (addr, allowed, running, has_addr, has_route) != (
        OLD_ADDR, OLD_ALLOWED, OLD_ALLOWED, False, False):
        fail("Unexpected state; do not modify a partially configured interface")
    v4before = cmd("ip", "-4", "route", "get", "10.99.0.1")
    if " dev " + IFACE + " " not in " " + v4before:
        fail("Original IPv4 route is not through the expected WireGuard interface")

    def modify(key, new_value):
        index = fields[key][1]
        old_line = lines[index]
        original_value = fields[key][0]
        pos = old_line.index(original_value)
        lines[index] = old_line[:pos] + new_value + old_line[pos + len(original_value):]

    modify(("Interface", "address"), ", ".join(NEW_ADDR))
    modify(("Peer", "allowedips"), ", ".join(NEW_ALLOWED))
    updated = "".join(lines)
    _lines, _fields, target_address, target_allowed = entries(updated)
    if target_address != NEW_ADDR or target_allowed != NEW_ALLOWED:
        fail("New configuration failed validation")

    BACKUP_DIR.mkdir(mode=0o700, exist_ok=True)
    backup = BACKUP_DIR / ("wg-btcnode2.conf.before-ula-" +
                            dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if backup.exists():
        fail("Backup name collision")
    shutil.copy2(CONF, backup)
    backup.chmod(0o600)
    print("BACKUP_OK=" + str(backup), flush=True)
    original_stat = CONF.stat()
    temporary = None
    persisted = False
    ip_added = False
    route_added = False
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ROOT,
                                         prefix=".wg-btcnode2-ula-", delete=False) as tmp:
            temporary = Path(tmp.name)
            tmp.write(updated)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.chmod(temporary, 0o600)
        os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        if CONF.read_text(encoding="utf-8") != config:
            fail("Configuration changed during operation")
        os.replace(temporary, CONF)
        temporary = None
        persisted = True
        cmd("ip", "-6", "address", "add", CLIENT_ULA, "dev", IFACE)
        ip_added = True
        cmd("wg", "set", IFACE, "peer", GW_KEY, "allowed-ips", ",".join(NEW_ALLOWED))
        cmd("ip", "-6", "route", "add", GW_ULA, "dev", IFACE, "src", CLIENT_ULA.split("/")[0])
        route_added = True
        result = show_current()
        if (result[3], result[4], result[5], result[6], result[7]) != (
                NEW_ADDR, NEW_ALLOWED, NEW_ALLOWED, True, True):
            fail("Post-change runtime state verification failed")
        if cmd("ip", "-4", "route", "get", "10.99.0.1") != v4before:
            fail("IPv4 route changed unexpectedly")
        print("APPLY_OK client_ula_active=yes gateway_ula_route=yes ipv4_preserved=yes")
        print("WG_RESTART=no PUBLIC_IPV6_CHANGED=no")
        return 0
    except Exception:
        print("APPLY_FAILED attempting limited rollback", file=sys.stderr)
        try:
            if route_added:
                cmd("ip", "-6", "route", "del", GW_ULA, "dev", IFACE)
            cmd("wg", "set", IFACE, "peer", GW_KEY, "allowed-ips", ",".join(OLD_ALLOWED))
            if ip_added:
                cmd("ip", "-6", "address", "del", CLIENT_ULA, "dev", IFACE)
            if persisted:
                shutil.copy2(backup, CONF)
            print("ROLLBACK_OK original ipv4 config restored", file=sys.stderr)
        except Exception as err:
            print("ROLLBACK_FAILED backup=" + str(backup) + " error=" + str(err), file=sys.stderr)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as err:
        print("ERROR:", str(err), file=sys.stderr)
        sys.exit(1)
