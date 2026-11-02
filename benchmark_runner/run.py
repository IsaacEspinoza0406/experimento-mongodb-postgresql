"""Carga multiproceso en cgroup separado. Una invocación = un intento, nunca sobreescrito."""
import argparse
import json
import multiprocessing as mp
import queue
import time
import traceback
from pathlib import Path
from .db import connect, prepare, query
from .metrics import summarize


def worker(index, config, gate, schedule, ready):
    path = Path(config['output']) / f'worker-{index:02d}.jsonl'
    connection = None
    try:
        connection = connect(config['engine'])
        prepare(config['engine'], connection, config['variant'])
        ready.put({'worker': index, 'ready': True})
        gate.wait()
        base, measurement_start, measurement_end = schedule[:]
        with path.open('x', buffering=1) as stream:
            while time.perf_counter() < measurement_end:
                start = time.perf_counter()
                epoch = time.time()
                ok, count, error = False, None, None
                try:
                    rows = query(config['engine'], connection, config['query_id'], config['variant'])
                    count, ok = len(rows), True
                except Exception as exc:
                    # Mensaje de servidor sin DSN/credenciales; conexión no se recrea silenciosamente.
                    error = {'type': type(exc).__name__, 'message': str(exc)[:2000]}
                end = time.perf_counter()
                row = {'worker': index, 'start': start, 'end': end, 'epoch_start': epoch,
                       'duration_ms': (end - start) * 1000, 'ok': ok, 'rows': count,
                       'phase_start': 'warmup' if start < measurement_start else 'measure', 'error': error}
                stream.write(json.dumps(row) + '\n')
                # Evitar spin en un fallo de socket; un fallo no representa nuevas solicitudes útiles.
                if not ok:
                    if error['type'] in ('InterfaceError', 'NetworkTimeout', 'AutoReconnect', 'ServerSelectionTimeoutError'):
                        break
                    time.sleep(.01)
    except BaseException:
        ready.put({'worker': index, 'ready': False, 'error': traceback.format_exc()})
        raise
    finally:
        if connection:
            connection.close()


def run(config):
    output = Path(config['output'])
    output.mkdir(parents=True, exist_ok=False)
    (output / 'config.json').write_text(json.dumps(config, indent=2))
    ctx = mp.get_context('spawn')
    gate, ready = ctx.Event(), ctx.Queue()
    schedule = ctx.Array('d', [0., 0., 0.])
    workers = [ctx.Process(target=worker, args=(i, config, gate, schedule, ready)) for i in range(config['concurrency'])]
    try:
        for process in workers:
            process.start()
        for _ in workers:
            result = ready.get(timeout=180)
            if not result['ready']:
                raise RuntimeError(result)
        base = time.perf_counter() + .2
        schedule[:] = [base, base + config['warmup'], base + config['warmup'] + config['measure']]
        anchor = {'monotonic': time.perf_counter(), 'epoch': time.time()}
        (output / 'window.json').write_text(json.dumps({'start': schedule[1], 'end': schedule[2], 'anchor': anchor}))
        gate.set()
        deadline = schedule[2] + 130
        for process in workers:
            process.join(timeout=max(0, deadline-time.perf_counter()))
        if any(p.is_alive() for p in workers):
            raise TimeoutError('No se drenaron solicitudes tras 130 s; intento inválido')
        if any(p.exitcode for p in workers):
            raise RuntimeError('Proceso de carga falló')
        while time.perf_counter() < schedule[2] + config['cooldown']:
            time.sleep(min(1, max(0, schedule[2] + config['cooldown'] - time.perf_counter())))
        requests = []
        for path in output.glob('worker-*.jsonl'):
            with path.open() as stream:
                requests.extend(json.loads(line) for line in stream)
        summary = summarize(requests, schedule[1], schedule[2])
        summary.update(workers=len(workers), errors_total=sum(not r['ok'] for r in requests),
                       request_count=len(requests), finished_epoch=time.time())
        with (output / 'summary.json').open('x') as stream:
            json.dump(summary, stream, indent=2)
        return summary
    except BaseException:
        (output / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        for process in workers:
            if process.is_alive():
                process.terminate()
            if process.pid:
                process.join(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', choices=['mongodb', 'postgresql'], required=True)
    parser.add_argument('--query-id', choices=['Q1', 'Q2'], required=True)
    parser.add_argument('--variant', required=True)
    parser.add_argument('--concurrency', type=int, choices=[1, 10, 50], required=True)
    parser.add_argument('--warmup', type=float, default=60)
    parser.add_argument('--measure', type=float, default=300)
    parser.add_argument('--cooldown', type=float, default=60)
    parser.add_argument('--output', required=True)
    args = vars(parser.parse_args())
    if min(args['warmup'], args['measure'], args['cooldown']) <= 0:
        parser.error('duraciones deben ser positivas')
    print(json.dumps(run(args), indent=2))
