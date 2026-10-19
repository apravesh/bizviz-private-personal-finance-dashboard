#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export BIZVIZ_PLATFORM=fake

step() { printf '\n=== %s ===\n' "$1"; }

step "ruff check"
uv run ruff check .
step "ruff format --check"
uv run ruff format --check .
step "mypy src"
uv run mypy src
step "pytest"
uv run pytest -q
step "web: svelte-check"
(cd web && npm run check)
step "web: vitest"
(cd web && npm test -- --run)
step "web: build"
(cd web && npm run build)
