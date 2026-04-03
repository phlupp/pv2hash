#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-phlupp/pv2hash}"
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

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

TMP_DIR=""
API_URL=""
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
    require_command systemctl
    require_command runuser
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

github_api_get() {
    local url="$1"

    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        curl -fsSL \
            -H "Accept: application/vnd.github+json" \
            -H "Authorization: Bearer ${GITHUB_TOKEN}" \
            "${url}"
    else
        curl -fsSL \
            -H "Accept: application/vnd.github+json" \
            "${url}"
    fi
}

download_file() {
    local url="$1"
    local outfile="$2"

    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        curl -fsSL \
            -H "Authorization: Bearer ${GITHUB_TOKEN}" \
            -o "${outfile}" \
            "${url}"
    else
        curl -fsSL \
            -o "${outfile}" \
            "${url}"
    fi
}

fetch_release_metadata() {
    if [[ "${TAG}" == "latest" ]]; then
        API_URL="https://api.github.com/repos/${REPO}/releases/latest"
    else
        API_URL="https://api.github.com/repos/${REPO}/releases/tags/${TAG}"
    fi

    github_api_get "${API_URL}" > "${TMP_DIR}/release.json"

    readarray -t RELEASE_INFO < <(python3 - "${TMP_DIR}/release.json" <<'PY'
import json
import sys
from pathlib import Path

release = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

tag_name = release.get("tag_name") or ""
if not tag_name:
    raise SystemExit("Release hat keinen tag_name")

assets = release.get("assets") or []

archive = None
manifest = None
sha256 = None

for asset in assets:
    name = asset.get("name") or ""
    if name.startswith("pv2hash-") and name.endswith(".tar.gz"):
        archive = asset
    elif name == "manifest.json":
        manifest = asset
    elif name == "SHA256SUMS":
        sha256 = asset

if archive is None:
    raise SystemExit("Release-Asset pv2hash-*.tar.gz nicht gefunden")
if manifest is None:
    raise SystemExit("Release-Asset manifest.json nicht gefunden")
if sha256 is None:
    raise SystemExit("Release-Asset SHA256SUMS nicht gefunden")

version_slug = tag_name[1:] if tag_name.startswith("v") else tag_name

print(tag_name)
print(version_slug)
print(archive["name"])
print(archive["browser_download_url"])
print(manifest["browser_download_url"])
print(sha256["browser_download_url"])
PY
)

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

    runuser -u "${APP_USER}" -- python3 -m venv "${TMP_RELEASE_DIR}/venv"
    runuser -u "${APP_USER}" -- "${TMP_RELEASE_DIR}/venv/bin/pip" install --upgrade pip wheel
    runuser -u "${APP_USER}" -- "${TMP_RELEASE_DIR}/venv/bin/pip" install -r "${TMP_RELEASE_DIR}/requirements.txt"

    rm -rf "${TMP_RELEASE_DIR}/data"
    ln -s "${APP_DATA_DIR}" "${TMP_RELEASE_DIR}/data"

    mv "${TMP_RELEASE_DIR}" "${RELEASE_DIR}"
    chown -h "${APP_USER}:${APP_GROUP}" "${RELEASE_DIR}/data"
}

write_install_info() {
    cat > "${INSTALL_INFO_FILE}" <<EOF
PV2HASH_INSTALL_MODE=release
PV2HASH_REPO=${REPO}
PV2HASH_TAG=${TAG_NAME}
PV2HASH_VERSION=${FULL_VERSION}
PV2HASH_VERSION_SLUG=${VERSION_SLUG}
PV2HASH_ARCHIVE_NAME=${ARCHIVE_NAME}
PV2HASH_RELEASE_DIR=${RELEASE_DIR}
PV2HASH_APP_ROOT=${APP_ROOT}
PV2HASH_DATA_DIR=${APP_DATA_DIR}
PV2HASH_HOST=${HOST}
PV2HASH_PORT=${PORT}
PV2HASH_INSTALLED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
}

write_systemd_unit() {
    cat > "${SERVICE_FILE}" <<EOF
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
EOF
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
    echo "Release dir:  ${RELEASE_DIR}"
    echo "Current:      ${CURRENT_LINK}"
    echo "Data dir:     ${APP_DATA_DIR}"
    echo "Service:      ${SERVICE_NAME}.service"
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
    activate_release
    write_install_info
    write_systemd_unit
    enable_and_restart_service
    show_result
}

main "$@"