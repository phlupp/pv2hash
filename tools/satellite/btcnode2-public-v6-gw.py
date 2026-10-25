#!/usr/bin/env python3
"""Stage/maintain public DATUM IPv6 on gateway, preserving all current WG peers.

GW ::2020/128 host route, eth0 proxy NDP, btcnode2 peer AllowedIPs,
and 2 strictly scoped rules in existing nftables forward chain.
Changes are backup-protected and restart neither WireGuard nor myNode.
Modes: --check --apply --status --ensure.
"""
from pathlib import Path
import datetime as dt
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import tempfile

WG_IF = "wg0"
WAN_IF = "eth0"
GW_KEY = "VNcOEVXGrZAidgrVUyKMCFo4wdQOUYZNKd+iuHCTMHA="
PUBLIC = "2a03:4000:10:317::2020"
PUBLIC_CIDR = PUBLIC + "/128"
MAIL6 = "2a03:4000:9:5c9:48bb:68ff:febd:e718"
OLD = ("10.99.0.51/32", "fd8b:6f2e:4a91:99::51/128")
NEW = OLD + (PUBLIC_CIDR,)
CONF = Path("/etc/wireguard/wg0.conf")
NFT_CONF = Path("/etc/nftables.conf")
BACKUPS = Path("/root/wireguard-btcnode2-backups")
TARGET = Path("/usr/local/sbin/btcnode2-public-v6-gw.py")
UNIT = Path("/etc/systemd/system/btcnode2-public-v6-gw.service")
NFT_INSERT_ANCHOR = "        # Diagnostic IPv6 ::99\n"
NFT_BEGIN = '        # btcnode2 public DATUM v6 (only TCP/23334 and trusted diagnostics)\n'
NFT_RULES = (
    NFT_BEGIN
    + '        iifname "eth0" oifname "wg0" ip6 daddr ' + PUBLIC
      + ' tcp dport 23334 ct state new accept comment "btcnode2-public-datum"\n'
    + '        iifname "eth0" oifname "wg0" ip6 saddr ' + MAIL6
      + ' ip6 daddr ' + PUBLIC
      + ' meta l4proto ipv6-icmp accept comment "btcnode2-public-mail-ping"\n\n'
)
UNIT_TEXT = """[Unit]
Description=Restore second DATUM node public IPv6 route and NDP on GW
After=wg-quick@wg0.service nftables.service
Requires=wg-quick@wg0.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/btcnode2-public-v6-gw.py --ensure

[Install]
WantedBy=multi-user.target
"""


def fail(message):
    raise RuntimeError(message)


def run(*argv, check=True):
    return subprocess.run(argv, capture_output=True, text=True, check=check)


def config_peer_state(contents):
    chunks = re.split(r"(?m)(?=^\s*\[\s*Peer\s*\]\s*(?:[#;].*)?$)", contents)
    records = []
    for i, chunk in enumerate(chunks):
        if not re.match(r"\s*\[\s*Peer\s*\]", chunk):
            continue
        pubkeys = re.findall(r"(?mi)^\s*PublicKey\s*=\s*([^#;\s]+)\s*(?:[#;].*)?$", chunk)
        matches = re.findall(r"(?mi)^(\s*AllowedIPs\s*=\s*)([^#;\r\n]*)(\s*(?:[#;].*)?)$", chunk)
        if len(pubkeys) != 1 or len(matches) != 1:
            fail("Config contains malformed peer block")
        pfx, orig, suf = matches[0]
        records.append((i, pubkeys[0], tuple(x.strip() for x in orig.split(",") if x.strip()),
                        pfx + orig + suf, pfx, suf))
    ours = [x for x in records if x[1] == GW_KEY]
    if len(ours) != 1:
        fail("Expected exactly one btcnode2 peer in gateway config")
    row = ours[0]
    if row[2] not in (OLD, NEW):
        fail("Unexpected gateway peer AllowedIPs")
    ournet = ipaddress.ip_network(PUBLIC_CIDR)
    for _, pubkey, cidrs, *_ in records:
        if pubkey != GW_KEY:
            for prefix in cidrs:
                try:
                    net = ipaddress.ip_network(prefix, strict=False)
                except ValueError:
                    fail("Malformed peer AllowedIPs in gateway config")
                if net.version == 6 and ournet.subnet_of(net):
                    fail("Public IPv6 overlaps another peer AllowedIPs")
    return chunks, row


def runtime_peer():
    rows = run("wg", "show", WG_IF, "allowed-ips").stdout.splitlines()
    peers = {}
    for row in rows:
        values = row.split()
        if values:
            peers[values[0]] = tuple(values[1:])
    if GW_KEY not in peers or peers[GW_KEY] not in (OLD, NEW):
        fail("Unexpected gateway live AllowedIPs")
    for key, ips in peers.items():
        if key != GW_KEY:
            for prefix in ips:
                net = ipaddress.ip_network(prefix, strict=False)
                if net.version == 6 and ipaddress.ip_network(PUBLIC_CIDR).subnet_of(net):
                    fail("Public IPv6 prefix already assigned to other runtime peer")
    return peers[GW_KEY]


def route_state():
    rows = run("ip", "-6", "route", "show", "table", "main", "exact", PUBLIC_CIDR).stdout.splitlines()
    if not rows:
        return False
    if len(rows) != 1 or (" dev " + WG_IF + " ") not in " " + rows[0] + " ":
        fail("Conflicting public IPv6 host route")
    return True


def proxy_state():
    rows = run("ip", "-6", "neigh", "show", "proxy", "dev", WAN_IF).stdout.splitlines()
    return any(line.split() and line.split()[0] == PUBLIC for line in rows)


def nft_state():
    current = NFT_CONF.read_text()
    if current.count(NFT_INSERT_ANCHOR) != 1:
        fail("Unexpected nftables forward-chain anchor")
    if current.count(NFT_BEGIN) > 1:
        fail("Duplicate btcnode2 nftables firewall block")
    if NFT_BEGIN in current and NFT_RULES not in current:
        fail("Modified nftables block; refuse automatic update")
    return current, NFT_RULES in current


def add_live_nft_rules():
    # Append only our two filtered forward rules; do not flush a production
    # gateway's potentially more recent in-memory nftables changes.
    run("/usr/sbin/nft", "add", "rule", "inet", "filter", "forward",
        "iifname", WAN_IF, "oifname", WG_IF, "ip6", "daddr", PUBLIC,
        "tcp", "dport", "23334", "ct", "state", "new", "accept",
        "comment", "btcnode2-public-datum")
    run("/usr/sbin/nft", "add", "rule", "inet", "filter", "forward",
        "iifname", WAN_IF, "oifname", WG_IF, "ip6", "saddr", MAIL6,
        "ip6", "daddr", PUBLIC, "meta", "l4proto", "ipv6-icmp",
        "accept", "comment", "btcnode2-public-mail-ping")


def remove_live_nft_rules():
    # Each target must carry our precise unique marker; never flush chain.
    listing = run("/usr/sbin/nft", "-a", "list", "chain", "inet", "filter", "forward").stdout
    for label in ("btcnode2-public-datum", "btcnode2-public-mail-ping"):
        for line in listing.splitlines():
            if label in line:
                match = re.search(r"# handle (\\d+)\\s*$", line)
                if not match:
                    fail("Cannot identify exact nftables rule handle for " + label)
                run("/usr/sbin/nft", "delete", "rule", "inet", "filter", "forward",
                    "handle", match.group(1))
                break


def firewall_live():
    rows = run("/usr/sbin/nft", "list", "chain", "inet", "filter", "forward").stdout
    return all(marker in rows for marker in ("btcnode2-public-datum", "btcnode2-public-mail-ping"))


def baseline():
    if not (CONF.is_file() and not CONF.is_symlink() and NFT_CONF.is_file() and not NFT_CONF.is_symlink()):
        fail("Unexpected gateway WireGuard/nftables config file type")
    for service in ("wg-quick@wg0.service", "nftables.service"):
        if run("systemctl", "is-active", service).stdout.strip() != "active":
            fail("Gateway service not active: " + service)
    if run("cat", "/proc/sys/net/ipv6/conf/all/forwarding").stdout.strip() != "1":
        fail("Gateway IPv6 forwarding disabled")
    if run("cat", "/proc/sys/net/ipv6/conf/eth0/proxy_ndp").stdout.strip() != "1":
        fail("Gateway eth0 proxy_ndp disabled")
    target = run("ip", "-6", "route", "get", "fd8b:6f2e:4a91:99::51").stdout
    if (" dev " + WG_IF) not in target:
        fail("Internal myNode IPv6 ULA not routed via WG")
    addresses = run("ip", "-6", "-o", "addr", "show").stdout
    if PUBLIC in addresses:
        fail("Do not add public /128 to gateway interface")
    cfg = CONF.read_text()
    chunks, record = config_peer_state(cfg)
    live = runtime_peer()
    nft_contents, nft_has = nft_state()
    return cfg, chunks, record, live, nft_contents, nft_has


def install(path, data, mode):
    if path.is_symlink():
        fail("Symlink not allowed at " + str(path))
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            fail("Existing file differs: " + str(path))
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                         prefix=".btcnode2-public-gw-", delete=False) as out:
            temporary = Path(out.name)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def ensure():
    state = baseline()
    if state[2][2] != NEW or state[3] != NEW or not state[5]:
        fail("Persistent public GW files/live WireGuard not in approved state")
    if not firewall_live():
        fail("Live nftables rules missing; investigate before reloading GW firewall")
    if not route_state():
        run("ip", "-6", "route", "add", PUBLIC_CIDR, "dev", WG_IF)
    if not proxy_state():
        run("ip", "-6", "neigh", "add", "proxy", PUBLIC, "dev", WAN_IF)
    if not route_state() or not proxy_state():
        fail("Gateway route or Proxy NDP verification failed")
    print("ENSURE_OK hostroute=yes proxy_ndp=yes nft_forward_filter=yes")
    print("IPV4_AND_EXISTING_PEERS_UNCHANGED=yes")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--status", "--apply", "--ensure"):
        print("Usage: sudo python3 btcnode2-public-v6-gw.py --check|--apply|--status|--ensure",
              file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        fail("Run with sudo on gateway gw")
    mode = sys.argv[1]
    config, chunks, record, live, nft_contents, nft_has = baseline()
    print("GW_PREFLIGHT_OK gw_ipv6_forward=yes proxy_ndp_enabled=yes")
    print("CONFIG_PEER=" + ("public_enabled" if record[2] == NEW else "ula_only"))
    print("LIVE_PEER=" + ("public_enabled" if live == NEW else "ula_only"))
    print("NFT_CONFIG_PUBLIC=" + ("yes" if nft_has else "no"))
    print("PUBLIC_ROUTE=" + ("yes" if route_state() else "no"))
    print("PUBLIC_NDP_PROXY=" + ("yes" if proxy_state() else "no"))
    if mode in ("--check", "--status"):
        print("CHECK_OK nothing_changed")
        return 0
    if mode == "--ensure":
        ensure()
        return 0
    if record[2] == NEW and live == NEW and nft_has:
        ensure()
        print("ALREADY_APPLIED")
        return 0
    if record[2] != OLD or live != OLD or nft_has or firewall_live() or route_state() or proxy_state():
        fail("Partial or foreign public configuration exists; refuse apply")
    # Validate the candidate nftables file before writing anything.
    new_nft = nft_contents.replace(NFT_INSERT_ANCHOR, NFT_RULES + NFT_INSERT_ANCHOR, 1)
    if new_nft == nft_contents:
        fail("Failed to insert nftables forward rules")
    chunks[record[0]] = chunks[record[0]].replace(
        record[3], record[4] + ", ".join(NEW) + record[5], 1
    )
    new_wg = "".join(chunks)
    if new_wg == config or config_peer_state(new_wg)[1][2] != NEW:
        fail("Peer config edit validation failed")
    with tempfile.NamedTemporaryFile("w", dir=NFT_CONF.parent,
                                     prefix=".nft-btcnode2-check-", delete=False) as f:
        candidate = Path(f.name)
        f.write(new_nft)
    try:
        verify = run("/usr/sbin/nft", "-c", "-f", str(candidate), check=False)
        if verify.returncode:
            fail("nftables syntax check failed: " + verify.stderr[:300])
    finally:
        candidate.unlink(missing_ok=True)
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_wg = BACKUPS / ("wg0.conf.before-btcnode2-public-" + stamp)
    backup_nft = BACKUPS / ("nftables.conf.before-btcnode2-public-" + stamp)
    if backup_wg.exists() or backup_nft.exists():
        fail("Backup path collision")
    shutil.copy2(CONF, backup_wg)
    shutil.copy2(NFT_CONF, backup_nft)
    for p in (backup_wg, backup_nft):
        p.chmod(0o600)
    print("BACKUP_WG_OK=" + str(backup_wg), flush=True)
    print("BACKUP_NFT_OK=" + str(backup_nft), flush=True)
    changed_wg = changed_nft = changed_route = changed_proxy = changed_live_nft = False
    try:
        # Keep current original file owner and mode.
        install_candidate(NFT_CONF, new_nft.encode(), NFT_CONF.stat())
        changed_nft = True
        changed_live_nft = True
        add_live_nft_rules()
        if not firewall_live():
            fail("New scoped nftables forward rules not active")
        install_candidate(CONF, new_wg.encode(), CONF.stat())
        changed_wg = True
        run("wg", "set", WG_IF, "peer", GW_KEY, "allowed-ips", ",".join(NEW))
        if runtime_peer() != NEW:
            fail("Runtime WG peer not updated")
        run("ip", "-6", "route", "add", PUBLIC_CIDR, "dev", WG_IF)
        changed_route = True
        run("ip", "-6", "neigh", "add", "proxy", PUBLIC, "dev", WAN_IF)
        changed_proxy = True
        ensure()
        install(TARGET, Path(__file__).read_bytes(), 0o755)
        install(UNIT, UNIT_TEXT.encode(), 0o644)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", UNIT.name)
        print("APPLY_OK peer=yes gateway_route=yes ndp=yes datum_only_nft=yes")
        print("GW_RESTART=no MYNODE_RESTART=no")
        return 0
    except Exception:
        print("APPLY_FAILED attempting rollback", file=sys.stderr)
        try:
            if changed_proxy:
                run("ip", "-6", "neigh", "del", "proxy", PUBLIC, "dev", WAN_IF)
            if changed_route:
                run("ip", "-6", "route", "del", PUBLIC_CIDR, "dev", WG_IF)
            if changed_wg:
                shutil.copy2(backup_wg, CONF)
                run("wg", "set", WG_IF, "peer", GW_KEY, "allowed-ips", ",".join(OLD))
            if changed_live_nft:
                remove_live_nft_rules()
            if changed_nft:
                shutil.copy2(backup_nft, NFT_CONF)
            print("ROLLBACK_OK previous public-GW state restored", file=sys.stderr)
        except Exception as e:
            print("ROLLBACK_FAILED inspect backups: " + str(e), file=sys.stderr)
        raise


def install_candidate(path, data, stat):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                         prefix=".btcnode2-public-gw-", delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, stat.st_mode & 0o777)
        os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("ERROR:", str(error), file=sys.stderr)
        sys.exit(1)
