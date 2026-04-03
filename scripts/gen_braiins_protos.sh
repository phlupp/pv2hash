#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
THIRD_PARTY_DIR="$ROOT_DIR/third_party/braiins-bos-plus-api"
PROTO_DIR="$THIRD_PARTY_DIR/proto"
OUT_DIR="$ROOT_DIR/pv2hash/vendor/braiins_api_stubs"

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "==> Repo-Root: $ROOT_DIR"
echo "==> Temporäres Verzeichnis: $TMP_DIR"

mkdir -p "$THIRD_PARTY_DIR"
mkdir -p "$OUT_DIR"

echo "==> Lade offizielles Braiins API Repo"
git clone --depth 1 https://github.com/braiins/bos-plus-api.git "$TMP_DIR/bos-plus-api"

echo "==> Aktualisiere vendorte Proto-Dateien"
rm -rf "$PROTO_DIR"
mkdir -p "$THIRD_PARTY_DIR"
cp -R "$TMP_DIR/bos-plus-api/proto" "$PROTO_DIR"

echo "==> Lösche alte generierte Python-Stubs"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

PROTO_FILES=()
while IFS= read -r -d '' file; do
    PROTO_FILES+=("$file")
done < <(find "$PROTO_DIR/bos" -name '*.proto' -print0 | sort -z)

if [ "${#PROTO_FILES[@]}" -eq 0 ]; then
    echo "Fehler: Keine Proto-Dateien gefunden unter $PROTO_DIR/bos" >&2
    exit 1
fi

echo "==> Generiere Python gRPC Stubs"
python -m grpc_tools.protoc \
    -I "$PROTO_DIR" \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    "${PROTO_FILES[@]}"

echo "==> Erzeuge __init__.py Dateien"
while IFS= read -r -d '' dir; do
    touch "$dir/__init__.py"
done < <(find "$OUT_DIR" -type d -print0)

echo "==> Fertig"
echo "Proto-Quellen:   $PROTO_DIR"
echo "Python-Stubs:    $OUT_DIR"
echo
echo "Nächster Schritt:"
echo "  source venv/bin/activate"
echo "  bash scripts/gen_braiins_protos.sh"