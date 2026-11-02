"""Exporta muestras reales de Prometheus; sin interpolar ausencias ni reinicios."""
import json
import math
import urllib.parse
import urllib.request
from .metrics import counter_delta


def api(query, epoch, base='http://127.0.0.1:19090'):
    url = base + '/api/v1/query?' + urllib.parse.urlencode({'query': query, 'time': epoch})
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)
    if payload.get('status') != 'success':
        raise RuntimeError(payload)
    return payload


def collect(container_id, start, end):
    names = {'cfs': 'container_cpu_cfs_throttled_seconds_total',
             'rss': 'container_memory_rss', 'cpu': 'container_cpu_usage_seconds_total'}
    result = {'start_epoch': start, 'end_epoch': end, 'container_id': container_id,
              'rss_metric_mapping': 'container_memory_rss (bytes); manual usa sufijo _bytes',
              'raw': {}, 'metrics': {}, 'limitations': []}
    for name, metric in names.items():
        selector = metric + '{id=~".*' + container_id + '.*"}'
        payload = api(selector + f'[{math.ceil(end-start)+5}s]', end + 1)
        result['raw'][name] = payload
        rows = payload['data']['result']
        if len(rows) != 1:
            result['limitations'].append(f'{metric}: {len(rows)} series, se requiere exactamente una')
            result['metrics'][name] = None
            continue
        samples = [(float(t), float(v)) for t, v in rows[0]['values']]
        if not all(math.isfinite(v) for _, v in samples):
            result['limitations'].append(f'{metric}: NaN/Inf')
            result['metrics'][name] = None
            continue
        inside = [(t, v) for t, v in samples if start <= t < end]
        before = [(t, v) for t, v in samples if t <= start]
        after = [(t, v) for t, v in samples if t <= end]
        gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
        if not inside or not before or not after or start-before[-1][0] > 2 or end-after[-1][0] > 2 or max(gaps, default=0) > 2.5:
            result['limitations'].append(f'{metric}: cobertura temporal insuficiente')
            result['metrics'][name] = None
            continue
        if name == 'rss':
            result['metrics'][name] = max(v for _, v in inside)
        else:
            resets = any(b[1] < a[1] for a, b in zip(samples, samples[1:]))
            result['metrics'][name] = None if resets else counter_delta(before[-1][1], after[-1][1])
            result.setdefault('boundary_offsets_seconds', {})[name] = [start-before[-1][0], end-after[-1][0]]
            if resets:
                result['limitations'].append(f'{metric}: reinicio de contador')
    for metric in ['node_memory_MemAvailable_bytes', 'node_memory_SwapFree_bytes', 'node_vmstat_pswpin', 'node_vmstat_pswpout', 'node_disk_read_bytes_total', 'node_disk_written_bytes_total']:
        result['raw'][metric] = api(metric + f'[{math.ceil(end-start)+5}s]', end+1)
    return result
