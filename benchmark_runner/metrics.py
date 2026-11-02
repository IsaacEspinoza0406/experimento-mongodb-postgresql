import csv
import math
from pathlib import Path

HEADER = 'timestamp_epoch,engine,profile,query_id,concurrency,run_number,throughput_qps,latency_p50_ms,latency_p95_ms,latency_p99_ms,cpu_throttled_seconds,max_rss_mb,disk_spill,status'.split(',')


def percentile(values, p):
    if not values:
        return None
    if not 0 <= p <= 1 or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('muestras/percentil inválidos')
    values = sorted(values)
    pos = (len(values) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def counter_delta(before, after):
    if before is None or after is None or after < before:
        return None
    return after - before


def summarize(requests, start, end):
    if end <= start:
        raise ValueError('ventana inválida')
    completed = [r for r in requests if r['ok'] and start <= r['end'] < end]
    inside = [r for r in completed if r['start'] >= start]
    latencies = [r['duration_ms'] for r in inside]
    return {'throughput_qps': len(completed) / (end - start),
            'latency_p50_ms': percentile(latencies, .5),
            'latency_p95_ms': percentile(latencies, .95),
            'latency_p99_ms': percentile(latencies, .99),
            'latency_samples': len(latencies), 'completed_in_window': len(completed),
            'warmup_crossings_completed': len(completed) - len(inside),
            'errors_in_window': sum(not r['ok'] and start <= r['end'] < end for r in requests),
            'crossings_end': sum(r['start'] < end <= r['end'] for r in requests)}


def write_csv(path, row):
    if set(row) != set(HEADER) or row['status'] not in ('ok', 'spill', 'oom', 'error'):
        raise ValueError('CSV esquema/status incorrecto')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=HEADER)
        writer.writeheader()
        writer.writerow(row)
