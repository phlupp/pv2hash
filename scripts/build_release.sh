#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

NODE_DIR="${REPO_ROOT}/frontend"
VERSION_FILE="pv2hash/version.py"

readarray -t VERSIONS < <(python3 - <<'PY'
import json
import re
from pathlib import Path
text=Path("pv2hash/version.py").read_text(encoding="utf-8")
match=re.search(r'^APP_VERSION\s*=\s*"([^"]+)"',text,re.MULTILINE)
if not match:
    raise SystemExit("APP_VERSION not set")
print(match.group(1))
print(json.loads(Path("frontend/package.json").read_text(encoding="utf-8"))["version"])
PY
)
CORE_VERSION="${VERSIONS[0]}"
GUI_VERSION="${VERSIONS[1]}"
TAG="${PV2HASH_RELEASE_ID:-v${CORE_VERSION}}"

readarray -t RELEASE < <(TAG="${TAG}" CORE_VERSION="${CORE_VERSION}" GUI_VERSION="${GUI_VERSION}" python3 - <<'PY'
import os
import re
from pv2hash.release_identity import parse_release_id
core=os.environ["CORE_VERSION"]
gui=os.environ["GUI_VERSION"]
tag=os.environ["TAG"]
if not re.fullmatch(r"\d+\.\d+\.\d+",core):
    raise SystemExit("Core version must be X.Y.Z")
if not re.fullmatch(r"\d+\.\d+\.\d+",gui):
    if os.getenv("PV2HASH_ALLOW_DEV_GUI")!="1":
        raise SystemExit("Release GUI version must be stable X.Y.Z")
    if not re.fullmatch(r"\d+\.\d+\.\d+-dev\.\d+",gui):
        raise SystemExit("Unknown development GUI version")
identity=parse_release_id(tag)
if identity.kind=="legacy" and identity.version!=core:
    raise SystemExit("Legacy release tag must equal Core version")
print(identity.tag)
print(identity.slug)
PY
)
TAG="${RELEASE[0]}"
VERSION_SLUG="${RELEASE[1]}"
DIST_ROOT="dist/${VERSION_SLUG}"
ARCHIVE_NAME="pv2hash-${VERSION_SLUG}.tar.gz"
ARCHIVE_PATH="${DIST_ROOT}/${ARCHIVE_NAME}"
MANIFEST_PATH="${DIST_ROOT}/manifest.json"
SHA256_PATH="${DIST_ROOT}/SHA256SUMS"

command -v npm >/dev/null || { echo "Node/npm is required to build the GUI" >&2; exit 1; }
echo "Building PV2Hash Core ${CORE_VERSION}, GUI ${GUI_VERSION}, release ${TAG}"

if [[ "${PV2HASH_BUILD_SKIP_NPM_CI:-0}" != "1" ]]; then
    ( cd "${NODE_DIR}" && npm ci )
else
    [[ -d "${NODE_DIR}/node_modules" ]] || { echo "Missing node_modules" >&2; exit 1; }
    echo "DEVELOPMENT ONLY: reusing installed node_modules."
fi

( cd "${NODE_DIR}" && npm test && npm run build )
[[ -s "${NODE_DIR}/dist/index.html" ]] || { echo "Missing GUI build" >&2; exit 1; }
[[ -d "${NODE_DIR}/dist/assets" ]] || { echo "Missing GUI assets" >&2; exit 1; }
grep -q '/ui-next/assets/' "${NODE_DIR}/dist/index.html" || {
    echo "GUI bundle has incorrect base URL" >&2
    exit 1
}

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGE_DIR}"' EXIT
mkdir -p "${STAGE_DIR}/pv2hash/gui"
cp -a "${NODE_DIR}/dist/." "${STAGE_DIR}/pv2hash/gui/"
TAG="${TAG}" CORE_VERSION="${CORE_VERSION}" GUI_VERSION="${GUI_VERSION}" \
  python3 - <<'PY_INFO' > "${STAGE_DIR}/pv2hash/build_info.json"
import json
import os
print(json.dumps({
    "release_id":os.environ["TAG"],
    "core_version":os.environ["CORE_VERSION"],
    "gui_version":os.environ["GUI_VERSION"],
},indent=2))
PY_INFO


mkdir -p "${DIST_ROOT}"
rm -f "${ARCHIVE_PATH}" "${MANIFEST_PATH}" "${SHA256_PATH}"

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
    --transform "s|^|pv2hash-${VERSION_SLUG}/|" \
    -czf "${ARCHIVE_PATH}" \
    pv2hash \
    scripts \
    third_party \
    requirements.txt \
    README.md \
    LICENSE \
    -C "${STAGE_DIR}" pv2hash/gui pv2hash/build_info.json

ARCHIVE_SHA256="$(sha256sum "${ARCHIVE_PATH}" | awk '{print $1}')"
TAG="${TAG}" VERSION_SLUG="${VERSION_SLUG}" \
  CORE_VERSION="${CORE_VERSION}" GUI_VERSION="${GUI_VERSION}" \
  ARCHIVE_NAME="${ARCHIVE_NAME}" ARCHIVE_SHA256="${ARCHIVE_SHA256}" \
  python3 - <<'PY' > "${MANIFEST_PATH}"
import json
import os
from datetime import datetime, UTC
manifest={
    "app":"pv2hash",
    "release_id":os.environ["TAG"],
    "tag":os.environ["TAG"],
    "version":os.environ["CORE_VERSION"],  # Legacy 0.9.5 installers need this.
    "version_semver":os.environ["CORE_VERSION"],
    "core_version":os.environ["CORE_VERSION"],
    "gui_version":os.environ["GUI_VERSION"],
    "version_slug":os.environ["VERSION_SLUG"],
    "asset_name":os.environ["ARCHIVE_NAME"],
    "asset_sha256":os.environ["ARCHIVE_SHA256"],
    "created_at_utc":datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00","Z"),
}
print(json.dumps(manifest,indent=2,ensure_ascii=False))
PY

(
    cd "${DIST_ROOT}"
    sha256sum "${ARCHIVE_NAME}" manifest.json > SHA256SUMS
)

echo "Release archive: ${ARCHIVE_PATH}"
echo "Manifest: ${MANIFEST_PATH}"
echo "SHA256: ${SHA256_PATH}"
echo "Components: Core ${CORE_VERSION} / GUI ${GUI_VERSION}"
