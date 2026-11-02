"""Recalcula CSV desde evidencia cruda; no acepta existencia de archivo como prueba."""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_runner.metrics import HEADER, summarize


def audit(path):
    path = Path(path)
    m = json.loads((path / 'manifest.json').read_text())
    raw = Path(m['raw'])
    window = json.loads((raw / 'window.json').read_text())
    requests = []
    files = sorted(raw.glob('worker-*.jsonl'))
    assert len(files) == m['cell']['concurrency'], 'No coincide número de clientes'
    for file in files:
        last_end = 0
        with file.open() as stream:
            for line in stream:
                r = json.loads(line)
                assert r['end'] >= r['start'] >= last_end, 'Tiempos no monótonos por cliente'
                assert math.isclose(r['duration_ms'], (r['end'] - r['start']) * 1000), 'Duración inconsistente'
                last_end = r['end']
                requests.append(r)
    summary = summarize(requests, window['start'], window['end'])
    with (path / 'metrics.csv').open(newline='') as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == HEADER, 'CSV encabezado incorrecto'
        rows = list(reader)
    assert len(rows) == 1
    for key in ['throughput_qps', 'latency_p50_ms', 'latency_p95_ms', 'latency_p99_ms']:
        if summary[key] is None:
            assert rows[0][key] == '', 'Ausencia convertida en valor'
        else:
            assert math.isclose(float(rows[0][key]), summary[key], rel_tol=1e-12), key
    assert m['status'] == 'complete', 'Intento no completo'
    assert not m['telemetry_limitations'], 'Telemetría incompleta'
    telemetry = json.loads((path / 'telemetry.json').read_text())
    for key, metric in [('cpu_throttled_seconds', 'cfs'), ('max_rss_mb', 'rss')]:
        value = telemetry['metrics'][metric]
        if metric == 'rss':
            value /= 1e6
        assert math.isclose(float(rows[0][key]), value, rel_tol=1e-12), key
    result = {'attempt_id': m['attempt_id'], 'passed': True, 'requests_checked': len(requests),
              'latency_samples': summary['latency_samples'], 'mode': m['mode'],
              'note': 'Smoke verifica infraestructura; no acredita rendimiento definitivo' if m['mode'] == 'smoke' else 'Datos y tiempos oficiales auditados; piloto separado de matriz'}
    with (path / 'audit.json').open('x') as output:
        json.dump(result, output, indent=2)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('path')
    print(json.dumps(audit(p.parse_args().path), indent=2))
