"""Vigilante independiente: cancela solo recursos etiquetados del proyecto a la hora límite."""
import argparse
import datetime as dt
import json
import os
import hashlib
import subprocess
import time
from pathlib import Path
from deadline_policy import policy


def call(args, timeout=15):
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
        return {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    except Exception as error:
        return {'error': str(error)}


def stop_project_load(project, evidence):
    root = Path(evidence)
    root.mkdir(parents=True, exist_ok=True)
    listing = call(['docker', 'ps', '--filter', f'label=com.docker.compose.project={project}', '--format', '{{.ID}}|{{.Names}}|{{.Label "com.docker.compose.service"}}'])
    events = [{'epoch': time.time(), 'action': 'list', 'result': listing}]
    rows = [r.split('|') for r in listing.get('stdout', '').splitlines() if r.count('|') == 2]
    for cid, name, service in rows:
        if service == 'postgres':
            sql = "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE datname='orders_bench' AND pid<>pg_backend_pid() AND state='active'"
            result = call(['docker', 'exec', cid, 'psql', '-U', 'benchmark', '-d', 'orders_bench', '-c', sql])
            events.append({'epoch': time.time(), 'action': 'cancel_postgres', 'name': name, 'result': result})
        elif service == 'mongo':
            js = 'db.currentOp(true).inprog.filter(o => (o.ns || "").startsWith("orders_bench.")).forEach(o => printjson(db.killOp(o.opid)))'
            result = call(['docker', 'exec', cid, 'mongosh', '--quiet', '--eval', js])
            events.append({'epoch': time.time(), 'action': 'cancel_mongo', 'name': name, 'result': result})
    # Detener clientes y motores propios; exporters quedan para recuperar evidencia.
    ids = [cid for cid, _, service in rows if service in ('runner', 'mongo', 'postgres')]
    if ids:
        events.append({'epoch': time.time(), 'action': 'stop',
                       'result': call(['docker', 'stop', '--time', '5', *ids], timeout=12)})
    remaining = call(['docker', 'ps', '--filter', f'label=com.docker.compose.project={project}', '--format', '{{.ID}}|{{.Label "com.docker.compose.service"}}'])
    active = [r.split('|')[0] for r in remaining.get('stdout', '').splitlines() if r.split('|')[-1] in ('runner', 'mongo', 'postgres')]
    if active:
        # Último recurso antes de hard deadline: evidencia incompleta, nunca resultado válido.
        events.append({'epoch': time.time(), 'action': 'kill_after_grace', 'result': call(['docker', 'kill', *active], timeout=5)})
    verification = call(['docker', 'ps', '--filter', f'label=com.docker.compose.project={project}', '--format', '{{.ID}}|{{.Label "com.docker.compose.service"}}'],timeout=5)
    still_active = [r for r in verification.get('stdout','').splitlines() if r.split('|')[-1] in ('runner','mongo','postgres')]
    events.append({'epoch':time.time(),'action':'verify_zero_active','verified_zero':verification.get('returncode') == 0 and not still_active,'result':verification})
    with (root / 'cancellation-events.jsonl').open('a') as output:
        for event in events:
            output.write(json.dumps(event) + '\n')
    return events


def watch(project, root, cutoff, hard):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'process.json').open('x') as output:
        json.dump({'pid': os.getpid(), 'project': project, 'cutoff_epoch': cutoff, 'hard_epoch': hard, 'started_epoch': time.time(),
                   'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, output)
    while time.time() < cutoff:
        # Heartbeat es estado mutable; eventos/manifest de terminación son inmutables.
        temporary = root/'heartbeat.json.partial'
        temporary.write_text(json.dumps({'pid': os.getpid(), 'epoch': time.time(), 'status': 'watching'}))
        os.replace(temporary,root/'heartbeat.json')
        time.sleep(min(1, max(.01, cutoff-time.time())))
    events = stop_project_load(project, root)
    with (root / 'completed.json').open('x') as output:
        json.dump({'epoch': time.time(), 'hard_epoch': hard, 'before_hard_limit': time.time() <= hard,
                   'no_active_experimental_load':events[-1].get('verified_zero',False),'events': events}, output, indent=2)
    if project == 'eva02-jsonbench':
        # Análisis solo después de retirar carga; respaldo del cierre aunque el coordinador falle.
        import sys
        result = call([sys.executable,'scripts/report.py'],timeout=300)
        (root/'consolidation.json').write_text(json.dumps(result,indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--project', default='eva02-jsonbench')
    p.add_argument('--output', required=True)
    p.add_argument('--test-seconds', type=int)
    a = p.parse_args()
    config = policy()
    if a.test_seconds is not None:
        if a.project != 'eva02-deadline-test':
            p.error('Override solo para proyecto efímero de prueba')
        hard = time.time() + a.test_seconds
        cutoff = hard - 6
    else:
        hard, cutoff = config['hard_epoch'], config['load_cutoff_epoch']
    watch(a.project, a.output, cutoff, hard)
