#!/usr/bin/env bash
# btcnode2: WireGuard + administrator bootstrap (Debian/Raspberry Pi OS 64-bit).
# Public installer. SSH keys below are PUBLIC keys; no secrets are embedded.
set -Eeuo pipefail
umask 077
MODE="${1:---check}"
case "$MODE" in --check|--apply|--status) ;; *) echo 'Usage: sudo bash bootstrap-btcnode2.sh --check|--apply|--status' >&2; exit 2 ;; esac

IFACE='wg-btcnode2'
ADDRESS='10.99.0.51/32'
ENDPOINT='gw.phlupp.net:51820'
GW_KEY='i7RSqgfZi88gZFFzR6BZkR8OOeHFA8hWrjWuUYR3KFw='
# Initial split-tunnel: only gateway and designated future management host.
ALLOWED='10.99.0.1/32, 10.20.0.250/32'
ROOT='/etc/wireguard'
CONF="${ROOT}/${IFACE}.conf"
KEY="${ROOT}/${IFACE}.key"
PUB="${ROOT}/${IFACE}.pub"
LOG='/var/log/btcnode2-bootstrap.log'
MARKER='# Managed by btcnode2 bootstrap v1'
PHLUPP_KEYS=(
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAbhxSVdG+vkf37fjKSYFIK07LnYpRhsQ6kwazpKzln4 phlupp_SMA-NB10515'
  'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKPIwJqernoVHrYb+nlHsQY0o5gSzqIk49WWdEdEVXNP phlupp_iPad'
)

fail() { printf 'FEHLER: %s\n' "$*" >&2; exit 1; }
log() { printf '[btcnode2] %s\n' "$*"; }
(( EUID == 0 )) || fail 'Start mit sudo erforderlich.'
[[ -r /etc/os-release && -d /run/systemd/system ]] || fail 'Nur Linux mit systemd und /etc/os-release unterstuetzt.'
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in debian|raspbian|ubuntu) ;; *) fail "Nicht unterstuetzte Distribution: ${ID:-unbekannt}" ;; esac
case "$(uname -m)" in aarch64|arm64) ;; *) fail "Raspberry Pi 5 benoetigt ein ARM64-System." ;; esac
for target in "$ROOT" "$CONF" "$KEY" "$PUB" /etc/sudoers.d/91-btcnode2-phlupp; do
  [[ ! -L "$target" ]] || fail "Unsicherer Symlink: $target"
done
if [[ -e "$CONF" ]]; then
  grep -Fxq "$MARKER" "$CONF" || fail "$CONF existiert bereits und wird nicht ueberschrieben."
  [[ -s "$KEY" ]] || fail 'WireGuard-Config vorhanden, aber privater Schluessel fehlt.'
elif [[ -e "$KEY" ]]; then
  fail 'WireGuard-Key existiert ohne verwaltete Konfiguration. Manuelle Pruefung erforderlich.'
fi
if ip link show "$IFACE" >/dev/null 2>&1 && [[ ! -f "$CONF" ]]; then
  fail "Interface $IFACE existiert bereits ohne verwaltete Konfiguration."
fi

# Stop on a directly connected overlap; never hijack a site's local network.
for target in 10.99.0.1 10.20.0.250; do
  route="$(ip -4 route get "$target" 2>/dev/null)" || fail "Keine Route zu $target"
  if [[ "$route" != *' via '* && "$route" != *" dev $IFACE "* ]]; then
    fail "$target wird bereits direkt/lokal erreicht ($route). Netzwerk vor Installation pruefen."
  fi
done

log "Betriebssystem: ${PRETTY_NAME:-${ID:-?}}, Architektur: $(uname -m); Hostname: $(hostname)"
log "Tunnel: $ADDRESS, Endpoint: $ENDPOINT, nur $ALLOWED; MTU=1360"
if [[ "$MODE" == --check ]]; then
  log 'CHECK_OK: Keine Aenderungen am Host vorgenommen.'
  exit 0
fi
if [[ "$MODE" == --status ]]; then
  systemctl is-active "wg-quick@$IFACE.service" || :
  if [[ -f "$PUB" ]]; then printf 'wireguard_public_key=%s\n' "$(cat "$PUB")"; fi
  wg show "$IFACE" endpoints latest-handshakes allowed-ips 2>/dev/null || :
  exit 0
fi

touch "$LOG"
if getent group adm >/dev/null 2>&1; then
  chgrp adm "$LOG"
  chmod 0640 "$LOG"
else
  chmod 0600 "$LOG"
fi
exec > >(tee -a "$LOG") 2>&1
trap 'printf "[btcnode2] Abbruch (Exit %s), Diagnoseprotokoll %s\n" "$?" "$LOG" >&2' ERR
log "Starte btcnode2-Einrichtung auf $(hostname). Lokale Default-Route bleibt unveraendert."
DEFAULT_BEFORE="$(ip -4 route show default)"
[[ -n "$DEFAULT_BEFORE" ]] || fail 'Keine lokale IPv4-Default-Route.'
log "Bisherige IPv4-Default-Route: $(printf '%s' "$DEFAULT_BEFORE" | head -n 1)"

# Install only necessary missing packages.
need_packages=()
command -v wg >/dev/null 2>&1 || need_packages+=(wireguard-tools)
command -v wg-quick >/dev/null 2>&1 || need_packages+=(wireguard-tools)
command -v sshd >/dev/null 2>&1 || need_packages+=(openssh-server)
command -v visudo >/dev/null 2>&1 || need_packages+=(sudo)
command -v ip >/dev/null 2>&1 || need_packages+=(iproute2)
if (( ${#need_packages[@]} > 0 )); then
  export DEBIAN_FRONTEND=noninteractive
  log 'Installiere fehlende Systempakete.'
  apt-get update
  apt-get install -y "${need_packages[@]}"
fi
if ! command -v wg >/dev/null || ! command -v wg-quick >/dev/null || ! command -v sshd >/dev/null || ! command -v visudo >/dev/null; then
  fail 'Systempakete fehlen weiterhin.'
fi

ensure_user() {
  local user="$1" home
  if ! id "$user" >/dev/null 2>&1; then
    useradd -m -s /bin/bash "$user"
    passwd -l "$user" >/dev/null
    log "Benutzer $user neu angelegt (Passwort gesperrt)."
  else
    log "Bestehender Benutzer $user wird beibehalten."
  fi
  home="$(getent passwd "$user" | cut -d: -f6)"
  [[ -d "$home" && ! -L "$home" ]] || fail "Unsicheres Home von $user."
}
add_key() {
  local user="$1" public="$2" home dir file
  home="$(getent passwd "$user" | cut -d: -f6)"
  dir="$home/.ssh"; file="$dir/authorized_keys"
  [[ ! -L "$dir" && ! -L "$file" ]] || fail "Symlink in SSH-Pfad von $user."
  install -d -m 0700 -o "$user" -g "$(id -gn "$user")" "$dir"
  if [[ ! -e "$file" ]]; then
    install -m 0600 -o "$user" -g "$(id -gn "$user")" /dev/null "$file"
  fi
  [[ -f "$file" ]] || fail "Ungueltige authorized_keys fuer $user."
  if ! grep -Fxq "$public" "$file"; then printf '%s\n' "$public" >> "$file"; fi
  chown "$user:$(id -gn "$user")" "$file"
  chmod 0600 "$file"
}

ensure_user phlupp
for k in "${PHLUPP_KEYS[@]}"; do add_key phlupp "$k"; done
# Admin SSH only: phlupp gets NOPASSWD sudo. MCP access is configured later.
sudo_file='/etc/sudoers.d/91-btcnode2-phlupp'
sudo_tmp="$(mktemp /etc/sudoers.d/.btcnode2.XXXXXX)"
printf '# Managed by btcnode2 bootstrap v1\nphlupp ALL=(ALL:ALL) NOPASSWD: ALL\n' > "$sudo_tmp"
chmod 0440 "$sudo_tmp"
visudo -cf "$sudo_tmp" >/dev/null || fail 'sudoers-Validierung fehlgeschlagen.'
if [[ -f "$sudo_file" ]] && ! grep -Fxq "$MARKER" "$sudo_file"; then
  rm -f "$sudo_tmp"
  fail "Bestehende sudoers-Datei $sudo_file wird nicht ueberschrieben."
fi
mv -f "$sudo_tmp" "$sudo_file"
log 'phlupp: bestehende Laptop/iPad-SSH-Keys, NOPASSWD sudo.'

log 'MCP-Diagnosezugang wird spaeter separat mit eigenem Schluessel eingerichtet.'

install -d -m 0700 -o root -g root "$ROOT"
if [[ ! -e "$KEY" ]]; then
  wg genkey > "$KEY"
  chmod 0600 "$KEY"
fi
wg pubkey < "$KEY" > "$PUB"
chmod 0644 "$PUB"
if [[ ! -e "$CONF" ]]; then
  cat > "$CONF" <<EOF
$MARKER
[Interface]
Address = $ADDRESS
MTU = 1360
PrivateKey = $(cat "$KEY")

[Peer]
PublicKey = $GW_KEY
Endpoint = $ENDPOINT
AllowedIPs = $ALLOWED
PersistentKeepalive = 25
EOF
  chmod 0600 "$CONF"
fi

systemctl enable --now ssh.service >/dev/null
systemctl enable "wg-quick@$IFACE.service" >/dev/null
if ! systemctl is-active --quiet "wg-quick@$IFACE.service"; then
  systemctl start "wg-quick@$IFACE.service"
fi
log "WireGuard: $(systemctl is-active "wg-quick@$IFACE.service"), SSH: $(systemctl is-active ssh.service)"
DEFAULT_AFTER="$(ip -4 route show default)"
[[ "$DEFAULT_BEFORE" == "$DEFAULT_AFTER" ]] || fail 'IPv4-Default-Route wurde veraendert; pruefen!'
log "Aktuelle IPv4-Default-Route: $(printf '%s' "$DEFAULT_AFTER" | head -n 1)"
if wg show "$IFACE" latest-handshakes | awk '{if ($2+0>0) found=1} END {exit !found}'; then
  state='HANDSHAKE_VORHANDEN'
else
  state='WARTET_AUF_GATEWAY_FREIGABE'
fi
printf '\n===== DIESEN BLOCK AN PHLUPP WEITERGEBEN =====\n'
printf 'node=btcnode2\n'
printf 'hostname=%s\n' "$(hostname)"
printf 'wireguard_address=%s\n' "$ADDRESS"
printf 'wireguard_public_key=%s\n' "$(cat "$PUB")"
printf 'status=%s\n' "$state"
printf 'ssh_users=phlupp (sudo); MCP spaeter\n'
printf 'log=%s\n' "$LOG"
printf '===== ENDE =====\n\n'
log 'Installer abgeschlossen. Gateway-Peer erst nach Pruefung am gw freigeben.'
