#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"
source .venv/bin/activate
mkdir -p logs
python main.py run "$@" 2>&1 | tee "logs/manual_run_$(date +%Y%m%d_%H%M%S).log"
