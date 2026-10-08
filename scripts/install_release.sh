#!/usr/bin/env bash
set -euo pipefail

PV2HASH_UPDATE_BASE_URL="${PV2HASH_UPDATE_BASE_URL:-https://api.github.com/repos/phlupp/pv2hash}"
PV2HASH_UPDATE_BASE_URL="${UPDATE_BASE_URL:-${PV2HASH_UPDATE_BASE_URL}}"
PV2HASH_UPDATE_CHANNEL="${PV2HASH_UPDATE_CHANNEL:-stable}"
PV2HASH_UPDATE_CHANNEL="${CHANNEL:-${PV2HASH_UPDATE_CHANNEL}}"
TAG="${TAG:-latest}"

APP_USER="${APP_USER:-pv2hash}"
APP_GROUP="${APP_GROUP:-pv2hash}"

APP_ROOT="${APP_ROOT:-/opt/pv2hash}"
RELEASES_DIR="${APP_ROOT}/releases"
CURRENT_LINK="${APP_ROOT}/current"

DATA_ROOT="${DATA_ROOT:-/var/lib/pv2hash}"
APP_DATA_DIR="${DATA_ROOT}/data"
LOG_DIR="${DATA_ROOT}/logs"

CONFIG_DIR="${CONFIG_DIR:-/etc/pv2hash}"
INSTALL_INFO_FILE="${CONFIG_DIR}/install.env"

SERVICE_NAME="pv2hash"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SELF_UPDATE_HELPER_PATH="/usr/local/libexec/pv2hash-self-update"
SELF_UPDATE_SUDOERS_FILE="/etc/sudoers.d/pv2hash-self-update"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

TMP_DIR=""
FEED_URL=""
TAG_NAME=""
VERSION_SLUG=""
FULL_VERSION=""
ARCHIVE_NAME=""
ARCHIVE_URL=""
MANIFEST_URL=""
SHA256_URL=""
RELEASE_DIR=""
TMP_RELEASE_DIR=""

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "Bitte als root oder mit sudo ausführen."
        exit 1
    fi
}

require_command() {
    local cmd="$1"
    if ! command -v "${cmd}" >/dev/null 2>&1; then
        echo "Fehler: benötigtes Kommando nicht gefunden: ${cmd}"
        exit 1
    fi
}

install_system_packages() {
    export DEBIAN_FRONTEND=noninteractive

    apt-get update
    apt-get install -y \
        ca-certificates \
        curl \
        sudo \
        tar \
        python3 \
        python3-venv \
        python3-pip
}

ensure_system_requirements() {
    require_command curl
    require_command tar
    require_command python3
    require_command sha256sum
    require_command sudo
    require_command systemctl
    require_command runuser
    require_command visudo
}

ensure_user_and_dirs() {
    if ! getent group "${APP_GROUP}" >/dev/null; then
        groupadd --system "${APP_GROUP}"
    fi

    if ! id -u "${APP_USER}" >/dev/null 2>&1; then
        useradd \
            --system \
            --gid "${APP_GROUP}" \
            --home-dir "${APP_ROOT}" \
            --create-home \
            --shell /usr/sbin/nologin \
            "${APP_USER}"
    fi

    mkdir -p "${RELEASES_DIR}"
    mkdir -p "${APP_DATA_DIR}"
    mkdir -p "${LOG_DIR}"
    mkdir -p "${CONFIG_DIR}"

    chown -R "${APP_USER}:${APP_GROUP}" "${APP_ROOT}"
    chown -R "${APP_USER}:${APP_GROUP}" "${DATA_ROOT}"
}

download_file() {
    local url="$1"
    local outfile="$2"

    curl -fsSL \
        -o "${outfile}" \
        "${url}"
}

normalize_tag() {
    local raw="$1"
    if [[ "${raw}" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        if [[ "${raw}" == v* ]]; then
            printf '%s' "${raw}"
        else
            printf 'v%s' "${raw}"
        fi
        return 0
    fi

    printf '%s' "${raw}"
}

fetch_release_metadata() {
    local base_url="${PV2HASH_UPDATE_BASE_URL%/}"

    if [[ "${base_url}" == "https://api.github.com/repos/phlupp/pv2hash" ]]; then
        if [[ "${TAG}" == "latest" ]]; then
            FEED_URL="${base_url}/releases/latest"
        else
            TAG="$(normalize_tag "${TAG}")"
            [[ "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
                echo "Ungültiger GitHub-Release-Tag: ${TAG}" >&2
                exit 1
            }
            FEED_URL="${base_url}/releases/tags/${TAG}"
        fi
        download_file "${FEED_URL}" "${TMP_DIR}/github-release.json"
        readarray -t RELEASE_INFO < <(python3 - "${TMP_DIR}/github-release.json" <<'PY'
import json
import re
import sys
from pathlib import Path

release = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
tag = str(release.get("tag_name") or "")
if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
    raise SystemExit("Ungültiger GitHub-Release-Tag")
if release.get("draft") or release.get("prerelease"):
    raise SystemExit("Nur veröffentlichte stabile GitHub-Releases sind erlaubt")
slug = tag[1:]
assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])}
archive = f"pv2hash-{slug}.tar.gz"
for name in (archive, "manifest.json", "SHA256SUMS"):
    if not assets.get(name):
        raise SystemExit(f"GitHub-Release enthält keine Datei {name}")
for value in (tag, slug, archive, assets[archive], assets["manifest.json"], assets["SHA256SUMS"]):
    print(value)
PY
)
        [[ "${#RELEASE_INFO[@]}" -eq 6 ]] || {
            echo "Fehler: unvollständige GitHub-Release-Metadaten" >&2
            exit 1
        }
        TAG_NAME="${RELEASE_INFO[0]}"
        VERSION_SLUG="${RELEASE_INFO[1]}"
        ARCHIVE_NAME="${RELEASE_INFO[2]}"
        ARCHIVE_URL="${RELEASE_INFO[3]}"
        MANIFEST_URL="${RELEASE_INFO[4]}"
        SHA256_URL="${RELEASE_INFO[5]}"
        RELEASE_DIR="${RELEASES_DIR}/${VERSION_SLUG}"
        TMP_RELEASE_DIR="${RELEASE_DIR}.tmp.$$"
        return
    fi

    if [[ "${TAG}" == "latest" ]]; then
        FEED_URL="${base_url}/channels/${PV2HASH_UPDATE_CHANNEL}.json"
        download_file "${FEED_URL}" "${TMP_DIR}/channel.json"

        readarray -t RELEASE_INFO < <(python3 - "${TMP_DIR}/channel.json" <<'PY'
import json
import sys
from pathlib import Path

feed = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

if feed.get("updates_enabled") is False:
    raise SystemExit(feed.get("message") or "Updates sind serverseitig deaktiviert")

latest = feed.get("latest")
if not isinstance(latest, dict):
    raise SystemExit("Update-Feed enthält kein latest-Release")

tag_name = str(latest.get("version_full") or latest.get("tag") or latest.get("version") or "").strip()
if not tag_name:
    raise SystemExit("Update-Feed enthält keinen Release-Tag")
if not tag_name.startswith("v"):
    tag_name = "v" + tag_name

asset = latest.get("asset") or {}
archive_name = str(asset.get("name") or "").strip()
archive_url = str(asset.get("url") or "").strip()
manifest_url = str(latest.get("manifest_url") or "").strip()
sha256_url = str(latest.get("checksums_url") or "").strip()

if not archive_name:
    raise SystemExit("Update-Feed enthält keinen Paketnamen")
if not archive_url:
    raise SystemExit("Update-Feed enthält keine Paket-URL")
if not manifest_url:
    raise SystemExit("Update-Feed enthält keine manifest_url")
if not sha256_url:
    raise SystemExit("Update-Feed enthält keine checksums_url")

version_slug = tag_name[1:] if tag_name.startswith("v") else tag_name

print(tag_name)
print(version_slug)
print(archive_name)
print(archive_url)
print(manifest_url)
print(sha256_url)
PY
)
    else
        TAG="$(normalize_tag "${TAG}")"
        TAG_NAME="${TAG}"
        VERSION_SLUG="${TAG_NAME#v}"
        MANIFEST_URL="${base_url}/releases/${TAG_NAME}/manifest.json"
        SHA256_URL="${base_url}/releases/${TAG_NAME}/SHA256SUMS"

        download_file "${MANIFEST_URL}" "${TMP_DIR}/manifest.probe.json"

        readarray -t RELEASE_INFO < <(python3 - "${TMP_DIR}/manifest.probe.json" "${MANIFEST_URL}" "${SHA256_URL}" "${base_url}" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
manifest_url = sys.argv[2]
sha256_url = sys.argv[3]
base_url = sys.argv[4].rstrip("/")

tag_name = str(manifest.get("tag") or "").strip()
if not tag_name:
    raise SystemExit("Manifest enthält keinen tag")
if not tag_name.startswith("v"):
    tag_name = "v" + tag_name

version_slug = str(manifest.get("version_slug") or (tag_name[1:] if tag_name.startswith("v") else tag_name)).strip()
archive_name = str(manifest.get("asset_name") or "").strip()
if not archive_name:
    raise SystemExit("Manifest enthält keinen asset_name")
archive_url = f"{base_url}/releases/{tag_name}/{archive_name}"

print(tag_name)
print(version_slug)
print(archive_name)
print(archive_url)
print(manifest_url)
print(sha256_url)
PY
)
    fi

    TAG_NAME="${RELEASE_INFO[0]}"
    VERSION_SLUG="${RELEASE_INFO[1]}"
    ARCHIVE_NAME="${RELEASE_INFO[2]}"
    ARCHIVE_URL="${RELEASE_INFO[3]}"
    MANIFEST_URL="${RELEASE_INFO[4]}"
    SHA256_URL="${RELEASE_INFO[5]}"
    RELEASE_DIR="${RELEASES_DIR}/${VERSION_SLUG}"
    TMP_RELEASE_DIR="${RELEASE_DIR}.tmp.$$"
}
download_and_verify_assets() {
    download_file "${ARCHIVE_URL}" "${TMP_DIR}/${ARCHIVE_NAME}"
    download_file "${MANIFEST_URL}" "${TMP_DIR}/manifest.json"
    download_file "${SHA256_URL}" "${TMP_DIR}/SHA256SUMS"

    (
        cd "${TMP_DIR}"
        sha256sum -c --ignore-missing SHA256SUMS
    )

    readarray -t MANIFEST_INFO < <(python3 - "${TMP_DIR}/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

print(manifest.get("version") or "")
print(manifest.get("tag") or "")
print(manifest.get("version_slug") or "")
print(manifest.get("asset_name") or "")
print(manifest.get("asset_sha256") or "")
PY
)

    FULL_VERSION="${MANIFEST_INFO[0]}"
    local manifest_tag="${MANIFEST_INFO[1]}"
    local manifest_version_slug="${MANIFEST_INFO[2]}"
    local manifest_asset_name="${MANIFEST_INFO[3]}"
    local manifest_asset_sha256="${MANIFEST_INFO[4]}"

    if [[ "${manifest_tag}" != "${TAG_NAME}" ]]; then
        echo "Fehler: manifest tag (${manifest_tag}) passt nicht zu Release-Tag (${TAG_NAME})"
        exit 1
    fi

    if [[ "${manifest_version_slug}" != "${VERSION_SLUG}" ]]; then
        echo "Fehler: manifest version_slug (${manifest_version_slug}) passt nicht zu ${VERSION_SLUG}"
        exit 1
    fi

    if [[ "${manifest_asset_name}" != "${ARCHIVE_NAME}" ]]; then
        echo "Fehler: manifest asset_name (${manifest_asset_name}) passt nicht zu ${ARCHIVE_NAME}"
        exit 1
    fi

    local actual_archive_sha256
    actual_archive_sha256="$(sha256sum "${TMP_DIR}/${ARCHIVE_NAME}" | awk '{print $1}')"

    if [[ "${manifest_asset_sha256}" != "${actual_archive_sha256}" ]]; then
        echo "Fehler: Archiv-Checksumme passt nicht zum Manifest"
        exit 1
    fi
}

extract_release() {
    if [[ -e "${RELEASE_DIR}" ]]; then
        echo "Fehler: Zielverzeichnis existiert bereits: ${RELEASE_DIR}"
        echo "Bitte alte Version löschen oder eine andere Release installieren."
        exit 1
    fi

    mkdir -p "${TMP_RELEASE_DIR}"

    tar -xzf "${TMP_DIR}/${ARCHIVE_NAME}" \
        -C "${TMP_RELEASE_DIR}" \
        --strip-components=1

    chown -R "${APP_USER}:${APP_GROUP}" "${TMP_RELEASE_DIR}"

    rm -rf "${TMP_RELEASE_DIR}/data"
    ln -s "${APP_DATA_DIR}" "${TMP_RELEASE_DIR}/data"

    mv "${TMP_RELEASE_DIR}" "${RELEASE_DIR}"
    chown -h "${APP_USER}:${APP_GROUP}" "${RELEASE_DIR}/data"

    runuser -u "${APP_USER}" -- python3 -m venv "${RELEASE_DIR}/venv"
    runuser -u "${APP_USER}" -- "${RELEASE_DIR}/venv/bin/pip" install --upgrade pip wheel
    runuser -u "${APP_USER}" -- "${RELEASE_DIR}/venv/bin/pip" install -r "${RELEASE_DIR}/requirements.txt"
}

write_install_info() {
    cat > "${INSTALL_INFO_FILE}" <<EOF_INFO
PV2HASH_INSTALL_MODE=release
PV2HASH_UPDATE_BASE_URL=${PV2HASH_UPDATE_BASE_URL}
PV2HASH_UPDATE_CHANNEL=${PV2HASH_UPDATE_CHANNEL}
PV2HASH_TAG=${TAG_NAME}
PV2HASH_VERSION=${FULL_VERSION}
PV2HASH_VERSION_SLUG=${VERSION_SLUG}
PV2HASH_ARCHIVE_NAME=${ARCHIVE_NAME}
PV2HASH_RELEASE_DIR=${RELEASE_DIR}
PV2HASH_APP_ROOT=${APP_ROOT}
PV2HASH_DATA_DIR=${APP_DATA_DIR}
PV2HASH_HOST=${HOST}
PV2HASH_PORT=${PORT}
PV2HASH_APP_USER=${APP_USER}
PV2HASH_APP_GROUP=${APP_GROUP}
PV2HASH_INSTALLED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF_INFO
}

install_self_update_helper() {
    local helper_source="${RELEASE_DIR}/scripts/pv2hash-self-update"

    if [[ ! -f "${helper_source}" ]]; then
        echo "Fehler: Update-Helper im Release nicht gefunden: ${helper_source}"
        exit 1
    fi

    install -d -m 0755 /usr/local/libexec
    install -m 0755 "${helper_source}" "${SELF_UPDATE_HELPER_PATH}"

    cat > "${SELF_UPDATE_SUDOERS_FILE}" <<EOF_SUDOERS
Cmnd_Alias PV2HASH_SELF_UPDATE = ${SELF_UPDATE_HELPER_PATH}, ${SELF_UPDATE_HELPER_PATH} *
${APP_USER} ALL=(root) NOPASSWD: PV2HASH_SELF_UPDATE
EOF_SUDOERS

    chmod 0440 "${SELF_UPDATE_SUDOERS_FILE}"
    visudo -cf "${SELF_UPDATE_SUDOERS_FILE}" >/dev/null
}

write_systemd_unit() {
    cat > "${SERVICE_FILE}" <<EOF_SERVICE
[Unit]
Description=PV2Hash
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${CURRENT_LINK}
Environment=PYTHONUNBUFFERED=1
ExecStart=${CURRENT_LINK}/venv/bin/uvicorn pv2hash.app:app --host ${HOST} --port ${PORT} --no-access-log
Restart=always
RestartSec=5
TimeoutStartSec=30
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF_SERVICE
}

activate_release() {
    ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}.new"
    mv -Tf "${CURRENT_LINK}.new" "${CURRENT_LINK}"
    chown -h "${APP_USER}:${APP_GROUP}" "${CURRENT_LINK}"
}

enable_and_restart_service() {
    systemctl daemon-reload
    systemctl enable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
    systemctl restart "${SERVICE_NAME}.service"
}

cleanup() {
    if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
        rm -rf "${TMP_DIR}"
    fi
    if [[ -n "${TMP_RELEASE_DIR}" && -d "${TMP_RELEASE_DIR}" ]]; then
        rm -rf "${TMP_RELEASE_DIR}"
    fi
}

show_result() {
    echo
    echo "PV2Hash wurde installiert/aktualisiert."
    echo "Version:      ${FULL_VERSION}"
    echo "Tag:          ${TAG_NAME}"
    echo "Update-Quelle: ${PV2HASH_UPDATE_BASE_URL%/}"
    echo "Release dir:  ${RELEASE_DIR}"
    echo "Current:      ${CURRENT_LINK}"
    echo "Data dir:     ${APP_DATA_DIR}"
    echo "Service:      ${SERVICE_NAME}.service"
    echo "Update-Helper:  ${SELF_UPDATE_HELPER_PATH}"
    echo "URL:          http://$(hostname -I | awk '{print $1}'):${PORT}/"
    echo
    echo "Status:       systemctl status ${SERVICE_NAME}"
    echo "Logs:         journalctl -u ${SERVICE_NAME} -f"
    echo
}

main() {
    trap cleanup EXIT

    require_root
    install_system_packages
    ensure_system_requirements
    ensure_user_and_dirs

    TMP_DIR="$(mktemp -d)"
    fetch_release_metadata
    download_and_verify_assets
    extract_release
    install_self_update_helper
    activate_release
    write_install_info
    write_systemd_unit
    enable_and_restart_service
    show_result
}

main "$@"
