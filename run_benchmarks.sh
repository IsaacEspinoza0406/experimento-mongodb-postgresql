#!/usr/bin/env sh
set -eu
# Cada celda se ejecuta explícitamente, solo tras verificar piloto y flush del kernel real.
# No arrancar 162 intentos sin validar puertas. Funciona desde shell con Python/Docker.
cd "$(dirname "$0")"
exec python scripts/orchestrate.py "$@"
