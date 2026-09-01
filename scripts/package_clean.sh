#!/usr/bin/env bash
# ============================================================
# package_clean.sh — Create clean handoff zip for this project
# Excludes: secrets, venv, node_modules, pycache, saved models,
#           previous zips, and generated data artifacts.
# Usage: bash scripts/package_clean.sh
# ============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT_DIR/dist/packages"
OUT_NAME="sales-analytics-ai-clean-$(date +%Y%m%d-%H%M%S).zip"
OUT_PATH="$OUT_DIR/$OUT_NAME"
STAGE_DIR="$(mktemp -d)"
STAGE_APP_DIR="$STAGE_DIR/sales-analytics-ai"

cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

mkdir -p "$OUT_DIR"
mkdir -p "$STAGE_APP_DIR"

echo "Packaging from: $ROOT_DIR"
echo "Output: $OUT_PATH"

rsync -a --delete \
  --exclude ".git/" \
  --exclude ".venv/" \
  --exclude "venv/" \
  --exclude "env/" \
  --exclude "node_modules/" \
  --exclude "frontend/node_modules/" \
  --exclude "frontend/dist/" \
  --exclude "frontend/.vite/" \
  --exclude "__pycache__/" \
  --exclude ".pytest_cache/" \
  --exclude ".mypy_cache/" \
  --exclude ".ruff_cache/" \
  --exclude "__MACOSX/" \
  --exclude "*.pyc" \
  --exclude "*.pyo" \
  --exclude "*.pkl" \
  --exclude "*.joblib" \
  --exclude "*.pt" \
  --exclude "*.onnx" \
  --exclude "*.log" \
  --exclude ".DS_Store" \
  --exclude "Thumbs.db" \
  --exclude ".env" \
  --exclude ".env.local" \
  --exclude ".env.production" \
  --exclude ".env.development" \
  --exclude ".env.test" \
  --exclude "backend/ml/saved/" \
  --exclude "reports/outputs/" \
  --exclude "reports/generated/" \
  --exclude "data/raw/" \
  --exclude "data/processed/" \
  --exclude "warehouse/" \
  --exclude "dist/packages/" \
  --exclude ".coverage" \
  --exclude "htmlcov/" \
  --exclude "*.zip" \
  --exclude "*.tar.gz" \
  "$ROOT_DIR/" "$STAGE_APP_DIR/"

# Include only a small representative sample of company data
if [ -d "$ROOT_DIR/sample_data" ]; then
  rsync -a "$ROOT_DIR/sample_data/" "$STAGE_APP_DIR/sample_data/"
fi

python "$ROOT_DIR/scripts/check_package_hygiene.py" --path "$STAGE_APP_DIR"

# ── Verification: fail if forbidden artifacts leaked into the package ─────
FORBIDDEN_PATTERNS=(".venv" "node_modules" ".git/" "__MACOSX" ".DS_Store" "__pycache__" ".pytest_cache" "htmlcov" ".coverage")
FOUND_VIOLATIONS=0
for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  if find "$STAGE_APP_DIR" -path "*${pattern}*" 2>/dev/null | grep -q .; then
    echo "ERROR: Forbidden artifact found in package: ${pattern}"
    FOUND_VIOLATIONS=1
  fi
done
if [ "$FOUND_VIOLATIONS" -eq 1 ]; then
  echo "Package verification FAILED — forbidden artifacts detected."
  exit 1
fi
echo "Package verification PASSED."

(
  cd "$STAGE_DIR"
  zip -rq "$OUT_PATH" "sales-analytics-ai"
)

echo ""
echo "Clean package created: $OUT_PATH"
SIZE_MB=$(du -sm "$OUT_PATH" | awk '{print $1}')
echo "Size: ${SIZE_MB}MB"
echo ""
echo "Contents (top-level only):"
unzip -l "$OUT_PATH" | grep -v "/$" | head -30 || true
echo ""
echo "Verify no secrets leaked:"
unzip -p "$OUT_PATH" .env 2>/dev/null && echo "WARNING: .env found in zip!" || echo "OK: .env not included"
