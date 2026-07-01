#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .venv312/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv312/bin/activate
fi

exec python app.py