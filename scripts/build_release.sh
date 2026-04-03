#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

VERSION_FILE="pv2hash/version.py"

if [[ ! -f "${VERSION_FILE}" ]]; then
    echo "Fehler: ${VERSION_FILE} nicht gefunden"
    exit 1
fi

readarray -t VERSION_INFO < <(python3 - <<'PY'
import re
from pathlib import Path

text = Path("pv2hash/version.py").read_text(encoding="utf-8")

def extract(name: str) -> str:
    m = re.search(rf'^{name}\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit(f"{name} nicht in pv2hash/version.py gefunden")
    return m.group(1)

version = extract("APP_VERSION")
build = extract("APP_BUILD")

full_version = f"{version}+build.{build}"
version_slug = f"{version}-build.{build}"
tag = f"v{version_slug}"

print(version)
print(build)
print(full_version)
print(version_slug)
print(tag)
PY
)

APP_VERSION="${VERSION_INFO[0]}"
APP_BUILD="${VERSION_INFO[1]}"
FULL_VERSION="${VERSION_INFO[2]}"
VERSION_SLUG="${VERSION_INFO[3]}"
TAG="${VERSION_INFO[4]}"

DIST_ROOT="dist/${VERSION_SLUG}"
PACKAGE_DIR_NAME="pv2hash-${VERSION_SLUG}"
ARCHIVE_NAME="${PACKAGE_DIR_NAME}.tar.gz"
ARCHIVE_PATH="${DIST_ROOT}/${ARCHIVE_NAME}"
MANIFEST_PATH="${DIST_ROOT}/manifest.json"
SHA256_PATH="${DIST_ROOT}/SHA256SUMS"

rm -rf "${DIST_ROOT}"
mkdir -p "${DIST_ROOT}"

echo "Baue Release-Paket für ${FULL_VERSION}"

tar \
    --exclude-vcs \
    --exclude='./dist' \
    --exclude='./data' \
    --exclude='./venv' \
    --exclude='./.venv' \
    --exclude='./__pycache__' \
    --exclude='./.pytest_cache' \
    --exclude='./.mypy_cache' \
    --exclude='./.ruff_cache' \
    --exclude='./.github' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --transform "s|^|${PACKAGE_DIR_NAME}/|" \
    -czf "${ARCHIVE_PATH}" \
    pv2hash \
    scripts \
    third_party \
    requirements.txt \
    README.md \
    LICENSE

ARCHIVE_SHA256="$(sha256sum "${ARCHIVE_PATH}" | awk '{print $1}')"

python3 - <<PY > "${MANIFEST_PATH}"
import json
from datetime import datetime, UTC
from pathlib import Path

manifest = {
    "app": "pv2hash",
    "version": "${FULL_VERSION}",
    "version_semver": "${APP_VERSION}",
    "build": "${APP_BUILD}",
    "version_slug": "${VERSION_SLUG}",
    "tag": "${TAG}",
    "asset_name": "${ARCHIVE_NAME}",
    "asset_sha256": "${ARCHIVE_SHA256}",
    "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}

print(json.dumps(manifest, indent=2, ensure_ascii=False))
PY

(
    cd "${DIST_ROOT}"
    sha256sum "$(basename "${ARCHIVE_PATH}")" "$(basename "${MANIFEST_PATH}")" > "$(basename "${SHA256_PATH}")"
)

echo
echo "Fertig."
echo "Version:      ${FULL_VERSION}"
echo "Tag:          ${TAG}"
echo "Archiv:       ${ARCHIVE_PATH}"
echo "Manifest:     ${MANIFEST_PATH}"
echo "Checksummen:  ${SHA256_PATH}"
echo