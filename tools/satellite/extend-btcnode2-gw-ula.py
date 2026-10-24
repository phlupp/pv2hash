#!/usr/bin/env python3
"""Safely extend only btcnode2's WireGuard AllowedIPs on gw; no interface restart."""
import datetime as dt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

IFACE = "wg0"
CONF = Path("/etc/wireguard/wg0.conf")
BACKUPS = Path("/root/wireguard-btcnode2-backups")
KEY = "VNcOEVXGrZAidgrVUyKMCFo4wdQOUYZNKd+iuHCTMHA="
OLD = ("10.99.0.51/32",)
NEW = ("10.99.0.51/32", "fd8b:6f2e:4a91:99::51/128")
TARGET6 = "fd8b:6f2e:4a91:99::51"


def run(*argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def fail(message):
    raise RuntimeError(message)


def peers_in_config(text):
    chunks = re.split(r"(?m)(?=^\s*\[\s*Peer\s*\]\s*(?:[#;].*)?$)", text)
    matches = []
    for i, chunk in enumerate(chunks):
        if not re.match(r"\s*\[\s*Peer\s*\]", chunk):
            continue
        chunk = re.split(r"(?m)(?=^\s*\[\s*(?!Peer\s*\])[^]\r\n]+\]\s*$)", chunk, maxsplit=1)[0]
        pk = re.findall(r"(?mi)^\s*PublicKey\s*=\s*([^#;\s]+)\s*(?:[#;].*)?$", chunk)
        if len(pk) != 1:
            fail("A peer has missing or duplicate PublicKey")
        aips = re.findall(r"(?mi)^(\s*AllowedIPs\s*=\s*)([^#;\r\n]*)(\s*(?:[#;].*)?)$", chunk)
        if len(aips) != 1:
            fail("A peer has missing or duplicate AllowedIPs")
        values = tuple(s.strip() for s in aips[0][1].split(",") if s.strip())
        matches.append((i, pk[0], values, aips[0]))
    return chunks, matches


def config_state(contents):
    chunks, peers = peers_in_config(contents)
    target = [(i, vals, match) for i, key, vals, match in peers if key == KEY]
    if len(target) != 1:
        fail("Expected exactly one btcnode2 peer in config; found %d" % len(target))
    for _, key, values, _ in peers:
        if key != KEY and any(v == NEW[1] for v in values):
            fail("New ULA already assigned to a different peer in config")
    i, values, match = target[0]
    if values not in (OLD, NEW):
        fail("Unexpected configured btcnode2 AllowedIPs: " + ",".join(values))
    return chunks, i, values, match


def live_state():
    results = {}
    for line in run("wg", "show", IFACE, "allowed-ips").splitlines():
        parts = line.split()
        if parts:
            results[parts[0]] = tuple(parts[1:])
    if KEY not in results:
        fail("btcnode2 peer missing in running WireGuard")
    for other, values in results.items():
        if other != KEY and NEW[1] in values:
            fail("ULA already assigned to a different live peer")
    values = results[KEY]
    if values not in (OLD, NEW):
        fail("Unexpected live btcnode2 AllowedIPs: " + ",".join(values))
    return values


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply", "--status"):
        print("Usage: sudo python3 extend-btcnode2-gw-ula.py --check|--apply|--status", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("Must run with sudo on gw")
    mode = sys.argv[1]
    if not CONF.is_file() or CONF.is_symlink():
        fail("Unexpected /etc/wireguard/wg0.conf file type")
    contents = CONF.read_text(encoding="utf-8")
    chunks, index, on_disk, matched = config_state(contents)
    running = live_state()
    route = run("ip", "-6", "route", "get", TARGET6)
    if " dev " + IFACE not in route:
        fail("IPv6 ULA route is not via wg0")
    if not run("systemctl", "is-active", "wg-quick@wg0.service").strip() == "active":
        fail("wg-quick@wg0 is not active")
    print("GW_PREFLIGHT_OK peer=btcnode2 iface=wg0 ipv6_route=wg0")
    print("CONFIG_ALLOWED_IPS=" + ",".join(on_disk))
    print("LIVE_ALLOWED_IPS=" + ",".join(running))
    if mode == "--status":
        return 0
    if on_disk != running:
        fail("Config and live peer differ; aborting without changes")
    if mode == "--check":
        print("CHECK_OK nothing_changed")
        return 0
    if on_disk == NEW:
        print("ALREADY_APPLIED nothing_changed")
        return 0
    before_stat = CONF.stat()
    prefix, original, suffix = matched
    chunks[index] = chunks[index].replace(
        prefix + original + suffix,
        prefix + ", ".join(NEW) + suffix,
        1,
    )
    after = "".join(chunks)
    if after == contents or after.count(KEY) != contents.count(KEY):
        fail("Unexpected peer editing result")
    _chunks2, _idx2, state2, _matched2 = config_state(after)
    if state2 != NEW:
        fail("Invalid new configuration")
    BACKUPS.mkdir(parents=True, mode=0o700, exist_ok=True)
    backup = BACKUPS / ("wg0.conf.before-btcnode2-ula-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if backup.exists():
        fail("Backup name collision")
    shutil.copy2(CONF, backup)
    backup.chmod(0o600)
    print("BACKUP_OK=" + str(backup), flush=True)
    temporary = None
    changed = False
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=CONF.parent, prefix=".wg0.btcnode2-ula-", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(after)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, before_stat.st_mode & 0o777)
        os.chown(temporary, before_stat.st_uid, before_stat.st_gid)
        if CONF.read_text(encoding="utf-8") != contents:
            fail("Configuration changed during operation")
        os.replace(temporary, CONF)
        temporary = None
        changed = True
        run("wg", "set", IFACE, "peer", KEY, "allowed-ips", ",".join(NEW))
        if live_state() != NEW:
            fail("Live WireGuard verification failed")
        config_state(CONF.read_text(encoding="utf-8"))
        print("APPLY_OK btcnode2_ipv4_preserved=yes ula_added=yes")
        print("GW_RESTART=no")
        return 0
    except Exception:
        if changed:
            print("APPLY_FAILED; rollback requested", file=sys.stderr)
            try:
                shutil.copy2(backup, CONF)
                run("wg", "set", IFACE, "peer", KEY, "allowed-ips", ",".join(OLD))
                print("ROLLBACK_OK", file=sys.stderr)
            except Exception as error:
                print("ROLLBACK_FAILED backup=%s cause=%s" % (backup, error), file=sys.stderr)
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
