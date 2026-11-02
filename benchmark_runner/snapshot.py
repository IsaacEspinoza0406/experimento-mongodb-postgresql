"""Evidencia específica del motor, fuera de ventana; sin inferencias por I/O genérico."""
import argparse
import json
from pathlib import Path
from .db import connect, prepare, ROOT


def snapshot(engine, variant, plans=False):
    connection = connect(engine)
    prepare(engine, connection, variant)
    if engine == 'mongodb':
        status = connection.admin.command('serverStatus')
        result = {'version': status.get('version'), 'metrics': status.get('metrics'),
                  'wiredTiger': status.get('wiredTiger'), 'uptime': status.get('uptime'),
                  'indexes': list(connection.orders_bench.orders.list_indexes())}
        if plans:
            result['plans'] = {}
            for query_id in ('Q1', 'Q2'):
                pipeline = json.loads((ROOT / 'queries/mongodb' / f'{query_id}.json').read_text())
                result['plans'][query_id] = connection.orders_bench.command('explain', {
                    'aggregate': 'orders', 'pipeline': pipeline, 'cursor': {},
                    'allowDiskUse': variant == 'disk', 'maxTimeMS': 120000}, verbosity='executionStats')
    else:
        cur = connection.cursor()
        cur.execute("SELECT version(),current_setting('work_mem'),current_setting('shared_buffers'),current_setting('max_parallel_workers_per_gather'),current_setting('jit')")
        result = {'settings': cur.fetchall()}
        cur.execute("SELECT temp_files,temp_bytes,stats_reset FROM pg_stat_database WHERE datname=current_database()")
        result['temp'] = cur.fetchall()
        cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename='orders'")
        result['indexes'] = cur.fetchall()
        if plans:
            result['plans'] = {}
            for query_id in ('Q1', 'Q2'):
                cur.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + (ROOT / 'queries/postgresql' / f'{query_id}.sql').read_text())
                result['plans'][query_id] = cur.fetchall()
        cur.close()
    connection.close()
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--engine', choices=['mongodb', 'postgresql'], required=True)
    p.add_argument('--variant', required=True)
    p.add_argument('--plans', action='store_true')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    with Path(a.output).open('x') as stream:
        json.dump(snapshot(a.engine, a.variant, a.plans), stream, indent=2, default=str)
