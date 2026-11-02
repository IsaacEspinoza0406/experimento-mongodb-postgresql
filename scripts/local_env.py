"""Crear secreto local exclusivo, sin mostrarlo ni reemplazar archivo existente."""
import secrets
from pathlib import Path

with Path('.env').open('x', encoding='utf-8') as stream:
    stream.write('BENCHMARK_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
print('.env local creado; secreto no mostrado.')
