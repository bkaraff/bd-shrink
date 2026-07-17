#!/usr/bin/env bash
# bd_shrink.sh — compatibility shim for bd_shrink v0.3.0+
# Forwards all arguments to the Python entrypoint: python -m bd_shrink
exec python3 -m bd_shrink "$@"
