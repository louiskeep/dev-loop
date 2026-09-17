#!/usr/bin/env bash
# Dispatch to a plugin hook module by basename, resolving ${CLAUDE_PLUGIN_ROOT} once.
set -euo pipefail
exec python3 "${CLAUDE_PLUGIN_ROOT}/hooks/$1.py"
