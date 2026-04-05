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

match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
if not match:
    raise SystemExit("APP_VERSION nicht in pv2hash/version.py gefunden")

version = match.group(1)

print(version)
print(version)
print(f"v{version}")
PY
)

APP_VERSION="${VERSION_INFO[0]}"
VERSION_SLUG="${VERSION_INFO[1]}"
TAG="${VERSION_INFO[2]}"
FULL_VERSION="${APP_VERSION}"

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

manifest = {
    "app": "pv2hash",
    "version": "${FULL_VERSION}",
    "version_semver": "${APP_VERSION}",
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
