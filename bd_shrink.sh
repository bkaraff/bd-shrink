#!/usr/bin/env bash
# bd_shrink.sh — compatibility shim for bd_shrink v0.3.0+
# Prefer Fedora's interpreter when its DNF-installed TUI dependencies are
# available. This avoids mixing Linuxbrew Python with /usr/lib site-packages.
PYTHON3=python3
if [[ -x /usr/bin/python3 ]] && /usr/bin/python3 -c \
    'import questionary, rich, wcwidth' >/dev/null 2>&1; then
    PYTHON3=/usr/bin/python3
fi
exec "$PYTHON3" -m bd_shrink "$@"
