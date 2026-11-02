import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


def get(path, params=None):
    url = 'http://127.0.0.1:19090' + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=15) as response:
        result = json.load(response)
    if result.get('status') != 'success':
        raise RuntimeError(result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    p.add_argument('--container-id', required=True)
    a = p.parse_args()
    names = ['container_cpu_cfs_throttled_seconds_total', 'container_memory_rss',
             'container_memory_rss_bytes', 'container_cpu_usage_seconds_total',
             'container_spec_cpu_quota', 'container_spec_memory_limit_bytes',
             'node_memory_MemTotal_bytes', 'node_memory_SwapTotal_bytes',
             'node_uname_info', 'node_disk_read_bytes_total']
    results = {'epoch': time.time(), 'targets': get('/api/v1/targets'), 'metrics': {}}
    for name in names:
        results['metrics'][name] = get('/api/v1/query', {'query': name})
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    with Path(a.output).open('x') as stream:
        json.dump(results, stream, indent=2)
    for name, result in results['metrics'].items():
        rows = result['data']['result']
        engine = [r for r in rows if a.container_id in r['metric'].get('id', '')]
        print(name, 'series=', len(rows), 'motor=', len(engine),
              'value=', engine[0]['value'][1] if engine else 'no disponible')
    assert all(t['health'] == 'up' for t in results['targets']['data']['activeTargets']), 'Exporters sin scrape'
    for metric in ['container_cpu_cfs_throttled_seconds_total', 'container_memory_rss', 'container_cpu_usage_seconds_total']:
        assert any(a.container_id in r['metric'].get('id', '') for r in results['metrics'][metric]['data']['result']), f'{metric} ausente para motor'
