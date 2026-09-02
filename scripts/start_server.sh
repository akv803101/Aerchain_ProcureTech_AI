#!/bin/bash
# Clear any inherited venv environment to prevent site module conflicts
unset VIRTUAL_ENV
unset PYTHONPATH
export PYTHONPATH=/Users/aakash/Desktop/aerchain/venv/lib/python3.12/site-packages
cd /Users/aakash/Desktop/aerchain
exec /Users/aakash/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12 -m uvicorn api.main:app --reload --port 8000
