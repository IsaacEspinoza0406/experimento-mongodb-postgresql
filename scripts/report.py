"""Salida de código: consolidación ligera, sin empaquetar reportes ni evidencias.

Adaptación de distribución posterior a las mediciones. No modifica el runner,
consultas, tiempos, criterios de validez ni métricas.
"""
import datetime as dt
from pathlib import Path
import subprocess
import sys


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    stamp = dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S%fZ')
    subprocess.run([sys.executable, str(root/'scripts/consolidate.py'),
                    '--mode', 'final', '--output', str(root/'output'/stamp)],
                   cwd=root, check=True)
