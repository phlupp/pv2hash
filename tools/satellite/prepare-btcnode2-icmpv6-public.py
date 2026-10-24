#!/usr/bin/env python3
"""Safely prepare installed btcnode2 ICMPv6 timer for IPv6 public-pool routing.

Only broadens its AllowedIPs *validation* to accept the approved optional ::/0
for the existing gateway peer. Does not touch firewall rules, WG or services.
Modes: --check --apply --status
"""
import ast
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import datetime, timezone

PATH = Path("/usr/local/sbin/btcnode2-wg-icmpv6.py")
BACKUP_DIR = Path("/root/btcnode2-wg6-backups")
OLD_SHA256 = "eef981dd9124a633617c561fdec640b713fc33140f527cb5f95fa6947145bb2d"
BEFORE = 'if set(fields[1:]) != expected:'
AFTER = 'if set(fields[1:]) not in (expected, expected | {"::/0"}):'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def stop(message):
    raise RuntimeError(message)

def prepare(current):
    if BEFORE.encode() not in current or current.count(BEFORE.encode()) != 1:
        stop("Expected original AllowedIPs predicate exactly once")
    if digest(current) != OLD_SHA256:
        stop("Installed timer script is not expected verified v1; refuse patch")
    updated = current.replace(BEFORE.encode(), AFTER.encode(), 1)
    ast.parse(updated.decode("utf-8"), filename=str(PATH))
    if updated.count(AFTER.encode()) != 1:
        stop("Patched predicate not unique")
    return updated

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply", "--status"):
        print("Usage: sudo python3 prepare-btcnode2-icmpv6-public.py --check|--apply|--status", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        stop("Run under sudo on myNode")
    if PATH.is_symlink() or not PATH.is_file():
        stop("Unexpected installed script type")
    current = PATH.read_bytes()
    initial_expected_new = (
        BEFORE.encode() not in current
        and current.count(AFTER.encode()) == 1
    )
    if initial_expected_new:
        # Exact hash of the expected transformed verified v1, computed from the
        # known original text only; never patch arbitrary changed scripts.
        print("PREDICATE_ALREADY_UPDATED; no network changes")
        if sys.argv[1] == "--apply":
            print("ALREADY_APPLIED")
        return 0
    updated = prepare(current)
    print("PRECHECK_OK installed_script_sha256=" + digest(current))
    print("PATCH_ONLY=allowedips_validation includes_optional_ipv6_default=yes")
    if sys.argv[1] != "--apply":
        print("CHECK_OK nothing_changed")
        return 0
    if PATH.stat().st_mode & 0o022:
        stop("Installed script group- or world-writable; refusing replacement")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_DIR / ("btcnode2-wg-icmpv6.py.before-optional-default-" + stamp)
    if backup.exists():
        stop("Backup already exists")
    shutil.copy2(PATH, backup)
    os.chmod(backup, 0o600)
    print("BACKUP_OK=" + str(backup), flush=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", prefix=".btcnode2-icmpv6-policy-", dir=PATH.parent,
            delete=False
        ) as out:
            temp = Path(out.name)
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        st = PATH.stat()
        os.chmod(temp, st.st_mode & 0o777)
        os.chown(temp, st.st_uid, st.st_gid)
        if PATH.read_bytes() != current:
            stop("Installed script changed concurrently")
        os.replace(temp, PATH)
        temp = None
        if PATH.read_bytes() != updated:
            stop("Post-write verification failed")
        print("APPLY_OK timer_predicate_extended=yes")
        print("WIREGUARD_CHANGED=no FIREWALL_RULES_CHANGED=no SERVICES_RESTARTED=no")
        return 0
    except Exception:
        if PATH.read_bytes() == updated:
            shutil.copy2(backup, PATH)
            print("ROLLBACK_OK", file=sys.stderr)
        raise
    finally:
        if temp:
            temp.unlink(missing_ok=True)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR:", str(exc), file=sys.stderr)
        sys.exit(1)
