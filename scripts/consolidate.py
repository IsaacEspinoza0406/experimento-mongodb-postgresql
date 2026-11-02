"""Auditoría de cobertura y CSV por variante. Nunca promedia percentiles."""
import argparse
import csv
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.matrix import matrix
from benchmark_runner.metrics import HEADER

p = argparse.ArgumentParser()
p.add_argument('--mode', choices=['smoke', 'pilot', 'final'], default='final')
p.add_argument('--output', required=True)
a = p.parse_args()
root = Path(a.output)
root.mkdir(parents=True, exist_ok=False)
groups, seen, attempts = {}, {}, []
for path in sorted((Path('results') / a.mode).glob('*/manifest.json')):
    m = json.loads(path.read_text())
    attempts.append({'key': m['key'], 'status': m['status'], 'path': path.as_posix()})
    if m['status'] != 'complete':
        continue
    if m['key'] in seen:
        raise ValueError('Duplicado ambiguo: ' + m['key'])
    seen[m['key']] = path.as_posix()
    with (path.parent / 'metrics.csv').open(newline='') as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == HEADER
        rows = list(reader)
        assert len(rows) == 1
    group = m['cell']['engine'] + '-' + m['cell']['query_id'] + '-' + m['cell']['variant']
    groups.setdefault(group, []).append(rows[0])
for name, rows in groups.items():
    with (root / (name + '.csv')).open('x', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)
expected = {c['key'] for c in matrix()} if a.mode == 'final' else set()
coverage = {'mode': a.mode, 'expected': len(expected), 'completed': len(seen),
            'missing': sorted(expected-set(seen)), 'unexpected': sorted(set(seen)-expected) if expected else [],
            'attempts': attempts, 'rows_by_variant': {k: len(v) for k,v in groups.items()}}
with (root / 'coverage.json').open('x') as output:
    json.dump(coverage, output, indent=2)
print(json.dumps({k:v for k,v in coverage.items() if k not in ['attempts','missing','unexpected']}))
