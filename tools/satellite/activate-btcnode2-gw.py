#!/usr/bin/env python3
"""One-time btcnode2 public-key migration on gw. Never restarts the WG interface."""
import datetime as dt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

OLD = "VDPXSHh+DMzO85tYBWqsGmrJikQlwV/RI4OJOX8c9jw="
NEW = "VNcOEVXGrZAidgrVUyKMCFo4wdQOUYZNKd+iuHCTMHA="
ADDR = "10.99.0.51/32"
IFACE = "wg0"
CONF = Path("/etc/wireguard/wg0.conf")
BACKUP_DIR = Path("/root/wireguard-btcnode2-backups")


def fail(msg):
    raise RuntimeError(msg)


def read_output(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def state():
    peers = read_output("wg", "show", IFACE, "peers").split()
    ips = {}
    for line in read_output("wg", "show", IFACE, "allowed-ips").splitlines():
        parts = line.split()
        if parts:
            ips[parts[0]] = parts[1:]
    return peers, ips


def replace_one_peer(text):
    lines = text.splitlines(keepends=True)
    blocks = []
    current = None
    for idx, line in enumerate(lines):
        if re.fullmatch(r"\s*\[\s*Peer\s*\]\s*(?:[#;].*)?", line.rstrip("\n")):
            current = {}
            blocks.append(current)
        elif re.match(r"\s*\[.+\]", line):
            current = None
        elif current is not None:
            match = re.fullmatch(r"\s*([A-Za-z]+)\s*=\s*([^#;\r\n]+?)\s*(?:[#;].*)?\r?\n?", line)
            if match:
                key = match.group(1).lower()
                if key in current:
                    fail("Duplicate option in peer: " + key)
                current[key] = (match.group(2).strip(), idx)
    old = [p for p in blocks if p.get("publickey", ("",))[0] == OLD]
    new = [p for p in blocks if p.get("publickey", ("",))[0] == NEW]
    if len(old) != 1 or new:
        fail("Config peer mismatch: old=%d new=%d" % (len(old), len(new)))
    options = old[0]
    if "presharedkey" in options:
        fail("PresharedKey detected; manual migration required")
    if set(options) != {"publickey", "allowedips"}:
        fail("Unexpected options in btcnode2 peer; abort without change")
    if [s.strip() for s in options["allowedips"][0].split(",")] != [ADDR]:
        fail("AllowedIPs mismatch; abort without change")
    index = options["publickey"][1]
    replaced = re.sub(r"(?<=\=)\s*" + re.escape(OLD), " " + NEW, lines[index], count=1)
    if replaced == lines[index] or replaced.count(NEW) != 1:
        fail("PublicKey replacement failed")
    lines[index] = replaced
    return "".join(lines)


def status():
    peers, ips = state()
    key = NEW if NEW in peers else OLD if OLD in peers else None
    print("current_btcnode2_peer=" + ("new" if key == NEW else "old" if key else "missing"))
    if key:
        print("allowed_ips=" + ",".join(ips.get(key, [])))
        stamps = [row.split()[1] for row in read_output("wg", "show", IFACE, "latest-handshakes").splitlines()
                  if len(row.split()) == 2 and row.split()[0] == key]
        print("latest_handshake_epoch=" + (stamps[0] if stamps else "unknown"))


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply", "--status"):
        print("Usage: sudo python3 activate-btcnode2-gw.py --check|--apply|--status", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("root privileges required (sudo)")
    mode = sys.argv[1]
    if mode == "--status":
        status()
        return 0
    if not CONF.is_file() or CONF.is_symlink():
        fail("Expected regular /etc/wireguard/wg0.conf, not symlink")
    before = CONF.read_text(encoding="utf-8")
    after = replace_one_peer(before)
    peers, ips = state()
    if peers.count(OLD) != 1 or NEW in peers or ips.get(OLD) != [ADDR]:
        fail("Live peer / AllowedIPs do not match config; abort")
    subprocess.run(["wg-quick", "strip", str(CONF)], check=True, capture_output=True)
    print("PREFLIGHT_OK interface=wg0 peer_count=%d target=%s" % (len(peers), ADDR))
    print("old_public_key=" + OLD)
    print("new_public_key=" + NEW)
    print("impact=one_peer_only no_service_restart")
    if mode == "--check":
        print("CHECK_OK nothing_changed")
        return 0
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = BACKUP_DIR / ("wg0.conf.before-btcnode2-mynode-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if backup.exists():
        fail("Backup name collision")
    shutil.copy2(CONF, backup)
    backup.chmod(0o600)
    print("BACKUP_OK=" + str(backup))
    changed = False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=CONF.parent,
                                         prefix=".wg0.conf.btcnode2-", delete=False) as tmp:
            temporary = Path(tmp.name)
            tmp.write(after)
            tmp.flush()
            os.fsync(tmp.fileno())
        temporary.chmod(0o600)
        os.chown(temporary, 0, 0)
        if CONF.read_text(encoding="utf-8") != before:
            fail("Configuration changed during operation")
        os.replace(temporary, CONF)
        temporary = None
        changed = True
        subprocess.run(["wg", "set", IFACE, "peer", NEW, "allowed-ips", ADDR], check=True)
        subprocess.run(["wg", "set", IFACE, "peer", OLD, "remove"], check=True)
        updated, updated_ips = state()
        if NEW not in updated or OLD in updated or updated_ips.get(NEW) != [ADDR]:
            fail("Peer verification failed")
        print("APPLY_OK saved_and_live_btcnode2_peer_switched")
        status()
        return 0
    except Exception:
        if changed:
            print("APPLY_FAILED attempting limited rollback", file=sys.stderr)
            try:
                shutil.copy2(backup, CONF)
                subprocess.run(["wg", "set", IFACE, "peer", NEW, "remove"], check=True)
                subprocess.run(["wg", "set", IFACE, "peer", OLD, "allowed-ips", ADDR], check=True)
                print("ROLLBACK_OK old peer restored", file=sys.stderr)
            except Exception as error:
                print("ROLLBACK_FAILED backup=%s reason=%s" % (backup, error), file=sys.stderr)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
