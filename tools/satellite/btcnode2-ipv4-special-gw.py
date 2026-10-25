#!/usr/bin/env python3
"""btcnode2 special IPv4 DATUM port on gw only.

Public 188.68.32.252:23335 -> wg0 10.99.0.51:23334, with SNAT to
10.99.0.1 so the client's reply follows the existing strict WireGuard
AllowedIPs instead of routing through Pueppi's home ISP.

--check is read-only on production rules/config; --apply makes backed-up,
targeted persistent nftables changes; --status reports; --rollback reverts
only the exact rules introduced by this helper when no foreign modifications.
Never flush the live nftables ruleset, touch myNode, or restart any service.
"""
from pathlib import Path
import datetime as dt
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

NFT = "/usr/sbin/nft"
CONF = Path("/etc/nftables.conf")
BACKUP_DIR = Path("/root/wireguard-btcnode2-backups")
WAN = "eth0"
WG = "wg0"
PUBLIC_IP = "188.68.32.252"
PUBLIC_PORT = 23335
TARGET_IP = "10.99.0.51"
TARGET_PORT = 23334
GW_WG_IP = "10.99.0.1"

FWD_ANCHOR = "        # btcnode2: MCP SSH -> independent Raspberry Pi only\n"
NAT_ANCHOR = "table ip nat {\n    chain postrouting {\n        type nat hook postrouting priority srcnat; policy accept;\n"

FWD_MARK = "btcnode2-v4-special-forward"
SNAT_MARK = "btcnode2-v4-special-snat"
DNAT_MARK = "btcnode2-v4-special-dnat"
FWD = (
    '        iifname "eth0" oifname "wg0" ip daddr 10.99.0.51 '
    'tcp dport 23334 ct status dnat ct state new accept '
    'comment "' + FWD_MARK + '"\n'
)
SNAT = (
    '        iifname "eth0" oifname "wg0" ip daddr 10.99.0.51 '
    'tcp dport 23334 ct status dnat snat to 10.99.0.1 '
    'comment "' + SNAT_MARK + '"\n'
)
DNAT = (
    '        iifname "eth0" ip daddr 188.68.32.252 '
    'tcp dport 23335 dnat to 10.99.0.51:23334 '
    'comment "' + DNAT_MARK + '"\n'
)
PRE = (
    "    chain prerouting {\n"
    "        type nat hook prerouting priority dstnat; policy accept;\n"
    + DNAT +
    "    }\n"
)
BANNER = "        # btcnode2 ONLY: special IPv4 DATUM port 23335 -> WG 10.99.0.51:23334\n"

# All three operations are one nft atomic transaction with no ruleset flush.
DELTA = (
    "add chain ip nat prerouting { type nat hook prerouting priority dstnat; policy accept; }\n"
    + "add rule inet filter forward "
    + 'iifname "eth0" oifname "wg0" ip daddr 10.99.0.51 tcp dport 23334 '
    + 'ct status dnat ct state new accept comment "' + FWD_MARK + '"\n"
    + "add rule ip nat postrouting "
    + 'iifname "eth0" oifname "wg0" ip daddr 10.99.0.51 tcp dport 23334 '
    + 'ct status dnat snat to 10.99.0.1 comment "' + SNAT_MARK + '"\n"
    + "add rule ip nat prerouting "
    + 'iifname "eth0" ip daddr 188.68.32.252 tcp dport 23335 '
    + 'dnat to 10.99.0.51:23334 comment "' + DNAT_MARK + '"\n"
)


def abort(msg):
    raise RuntimeError(msg)


def run(*cmd, check=True):
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def read_conf():
    if not CONF.is_file() or CONF.is_symlink():
        abort("Unexpected nftables config file")
    contents = CONF.read_text()
    if contents.count(FWD_ANCHOR) != 1 or contents.count("table ip nat {") != 1:
        abort("Expected gateway nftables anchors changed")
    markers = (FWD_MARK, SNAT_MARK, DNAT_MARK)
    present = [key in contents for key in markers]
    if any(present):
        if not all(present) or any(contents.count(key) != 1 for key in markers):
            abort("Partial or duplicate TCP/23335 configuration")
        if not (BANNER + FWD in contents and PRE in contents and SNAT in contents and
                "type nat hook postrouting priority srcnat; policy accept;\\n" in contents):
            abort("Persisted nftables rules differ from expected structure")
        return contents, True
    if contents.count(NAT_ANCHOR) != 1:
        abort("Expected original ip NAT postrouting anchor missing")
    if "chain prerouting {" in contents or "23335" in contents:
        abort("Foreign IPv4 prerouting chain or port 23335 already used")
    return contents, False

def candidate(conf):
    if conf.count(FWD_ANCHOR) != 1 or conf.count(NAT_ANCHOR) != 1:
        abort("Expected insertion anchors not unique")
    n = conf.replace(FWD_ANCHOR, BANNER + FWD + FWD_ANCHOR, 1)
    n = n.replace(
        NAT_ANCHOR,
        "table ip nat {\n" + PRE + "    chain postrouting {\n"
        "        type nat hook postrouting priority srcnat; policy accept;\n"
        + SNAT, 1
    )
    if n == conf or any(n.count(mark) != 1 for mark in (FWD_MARK,SNAT_MARK,DNAT_MARK)):
        abort("Candidate build failed")
    if "flush ruleset" not in n:
        abort("Original config unexpected (must retain existing bootstrap)")
    return n


def nft_runtime():
    # On fresh gw no IPv4 prerouting chain; normalize that known state.
    results = {}
    for label, table, chain in (
        (FWD_MARK, "inet filter", "forward"),
        (SNAT_MARK, "ip nat", "postrouting"),
        (DNAT_MARK, "ip nat", "prerouting"),
    ):
        proc = run(NFT, "-a", "list", "chain", *table.split(), chain, check=False)
        if proc.returncode and label == DNAT_MARK and "No such file or directory" in proc.stderr:
            results[label] = False
            continue
        if proc.returncode:
            abort("Could not inspect nft chain " + table + "/" + chain + ": " + proc.stderr[:200])
        results[label] = label in proc.stdout
    if any(results.values()) and not all(results.values()):
        abort("Partial live IPv4-special nftables rules detected")
    return results


def temporary_check(data):
    with tempfile.NamedTemporaryFile("w", prefix="btcnode2-v4-nft-", delete=False) as f:
        tmp = Path(f.name)
        f.write(data)
    try:
        rc = run(NFT, "-c", "-f", str(tmp), check=False)
        if rc.returncode:
            abort("nft preflight syntax failed: " + rc.stderr[:360])
    finally:
        tmp.unlink(missing_ok=True)


def baseline():
    for unit in ("nftables.service", "wg-quick@wg0.service"):
        if run("/usr/bin/systemctl", "is-active", unit).stdout.strip() != "active":
            abort("Required gateway service not active: " + unit)
    if "188.68.32.252/" not in run("/usr/sbin/ip", "-4", "-o", "addr", "show", "dev", WAN).stdout:
        abort("Gateway public IPv4 not on eth0")
    if run("/usr/bin/cat", "/proc/sys/net/ipv4/ip_forward").stdout.strip() != "1":
        abort("Gateway IPv4 forwarding not enabled")
    if " dev " + WG not in run("/usr/sbin/ip", "-4", "route", "get", TARGET_IP).stdout:
        abort("myNode IPv4 not routed via wg0")
    listeners = run("/usr/bin/ss", "-lntH").stdout
    if re.search(r":23335\s", listeners):
        abort("Public TCP/23335 already has a local listener")
    if not re.search(r":23334\s", listeners):
        abort("Primary TCP/23334 listener missing; preserve original primary")
    # Target socket test uses existing route and does not modify network config.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(4)
    try:
        s.connect((TARGET_IP, TARGET_PORT))
    except OSError as exc:
        abort("Cannot reach myNode DATUM inside WG: " + str(exc))
    finally:
        s.close()
    return True


def persistent_write(contents):
    st = CONF.stat()
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf8",
                                         prefix=".btcnode2-v4-", dir=CONF.parent,
                                         delete=False) as f:
            tmp = Path(f.name)
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, st.st_mode & 0o777)
        os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, CONF)
        tmp = None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def delete_live():
    # We delete only rules with the exact tags this helper owns.
    targets = [
        (DNAT_MARK, ["ip","nat","prerouting"]),
        (SNAT_MARK, ["ip","nat","postrouting"]),
        (FWD_MARK, ["inet","filter","forward"]),
    ]
    for marker, spec in targets:
        out = run(NFT, "-a", "list", "chain", *spec, check=False)
        if out.returncode:
            continue
        owned = [line for line in out.stdout.splitlines() if 'comment "'+marker+'"' in line]
        if len(owned) != 1:
            abort("Unable to identify exactly one owned rule: " + marker)
        match = re.search(r"# handle ([0-9]+)\s*$", owned[0])
        if not match:
            abort("Missing nft handle for " + marker)
        run(NFT, "delete", "rule", *spec, "handle", match.group(1))
    # We created the previously absent prerouting chain, so delete only if empty.
    out = run(NFT, "list", "chain", "ip", "nat", "prerouting", check=False)
    if out.returncode == 0:
        body = out.stdout.splitlines()
        if len(body) <= 4:
            run(NFT, "delete", "chain", "ip", "nat", "prerouting")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check","--apply","--status","--rollback"):
        print("Usage: sudo python3 btcnode2-ipv4-special-gw.py --check|--apply|--status|--rollback",file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        abort("Run as root on gw")
    mode = sys.argv[1]
    baseline()
    on_disk, installed = read_conf()
    live = nft_runtime()
    live_ok = all(live.values())
    print("GW_PREFLIGHT_OK ipv4_wan=yes wg_target_tcp23334=yes primary_tcp23334_untouched=yes")
    print("PERSISTENT_SPECIAL_IPV4=" + ("yes" if installed else "no"))
    print("LIVE_SPECIAL_IPV4_RULES=" + str(sum(live.values())) + "/3")
    if mode in ("--check","--status"):
        if installed != live_ok:
            abort("Persistent/live mismatch; refuse without inspection")
        if not installed:
            temporary_check(candidate(on_disk))
            temporary_check(DELTA)
        print("CHECK_OK nothing_changed")
        return 0
    if mode == "--apply":
        if installed and live_ok:
            print("ALREADY_APPLIED nothing_changed")
            return 0
        if installed or any(live.values()):
            abort("Unexpected partial setup, abort")
        updated = candidate(on_disk)
        temporary_check(updated)
        temporary_check(DELTA)
        BACKUP_DIR.mkdir(parents=True,exist_ok=True,mode=0o700)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup=BACKUP_DIR/("nftables.conf.before-btcnode2-ipv4-"+timestamp)
        if backup.exists():
            abort("Backup conflict")
        shutil.copy2(CONF, backup)
        backup.chmod(0o600)
        print("BACKUP_OK="+str(backup),flush=True)
        wrote = live_changed = False
        try:
            if CONF.read_text() != on_disk:
                abort("Config changed during preflight")
            persistent_write(updated)
            wrote = True
            with tempfile.NamedTemporaryFile("w",prefix="btcnode2-v4-live-",delete=False) as f:
                temp=Path(f.name); f.write(DELTA)
            try:
                run(NFT,"-f",str(temp))
                live_changed=True
            finally:
                temp.unlink(missing_ok=True)
            if not all(nft_runtime().values()):
                abort("Live rules not fully active")
            if read_conf()[1] is not True:
                abort("Persisted rules don't match")
            print("APPLY_OK public_ipv4_port=23335 target=10.99.0.51:23334")
            print("DNAT=yes SNAT=yes FILTER_ONLY_DATUM=yes REBOOT_PERSISTENCE=nftables.conf")
            print("NFT_RULESET_FLUSH=no WG_RESTART=no MYNODE_UNCHANGED=yes")
            return 0
        except Exception:
            if live_changed:
                try:delete_live()
                except Exception as exc:print("LIVE_ROLLBACK_FAILED "+str(exc),file=sys.stderr)
            if wrote:
                try:
                    persistent_write(on_disk)
                    print("CONFIG_ROLLBACK_OK",file=sys.stderr)
                except Exception as exc:print("CONFIG_ROLLBACK_FAILED "+str(exc),file=sys.stderr)
            raise
    # Explicit rollback allowed only when our complete live and config rules exist.
    if not installed and not any(live.values()):
        print("ALREADY_ROLLED_BACK")
        return 0
    if not installed or not live_ok:
        abort("Partial state; refuse rollback without operator")
    original = on_disk.replace(BANNER + FWD,"",1).replace(
        "table ip nat {\n"+PRE+"    chain postrouting {\n"
        "        type nat hook postrouting priority srcnat; policy accept;\n"+SNAT,
        NAT_ANCHOR, 1)
    if original == on_disk or any(k in original for k in (FWD_MARK,SNAT_MARK,DNAT_MARK)):
        abort("Unexpected structure for exact rollback")
    temporary_check(original)
    delete_live()
    persistent_write(original)
    print("ROLLBACK_OK special_tcp_23335_removed=yes primary_23334_unchanged=yes")
    return 0


if __name__ == "__main__":
    try:sys.exit(main())
    except Exception as error:
        print("ERROR:",error,file=sys.stderr);sys.exit(1)
