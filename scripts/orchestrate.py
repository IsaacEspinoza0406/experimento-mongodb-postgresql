"""Coordinador Windows/Linux sin deps. Cada intento/estado es persistente y exclusivo.

No ejecuta flush global automáticamente. --flush-receipt exige evidencia revisada para pilot/final.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.matrix import PROFILES, matrix
from benchmark_runner.metrics import HEADER, write_csv
from benchmark_runner.telemetry import collect
from kernel_flush import flush
from deadline_policy import admission, policy
from deadline_guard import stop_project_load
from config.delivery_schedule import schedule as delivery_schedule
from recovery import bounded, wait_healthy, ControllerState, pid_alive

CONTROLLER = None


def call(arguments, log=None, check=True, env=None):
    result = subprocess.run(arguments, capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
    if log:
        with Path(log).open('a', encoding='utf-8') as stream:
            stream.write(result.stdout + result.stderr)
    if check and result.returncode:
        raise RuntimeError(f'{arguments[0]} falló ({result.returncode}); ver log')
    return result.stdout.strip()


def compose(project, *args, log=None, env=None):
    return call(['docker', 'compose', '--env-file', '.env', '--env-file', 'config/images.env', '-p', project, *args], log, env=env)


def save(path, payload):
    temporary = path.with_suffix(path.suffix + '.partial')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2, default=str)
    os.replace(temporary, path)


def progress_update(manifest, event):
    now = dt.datetime.now(ZoneInfo('America/Mexico_City')).isoformat()
    records = []
    for path in Path('results/final').glob('*/manifest.json'):
        m = json.loads(path.read_text())
        audit_path = path.parent / 'audit.json'
        if m.get('status') == 'complete' and audit_path.exists() and json.loads(audit_path.read_text()).get('passed'):
            records.append(m)
    first = len({m['key'] for m in records if m['cell']['run_number'] == 1})
    text = f'''# Estado del trabajo

Actualizado automáticamente {now}. Evento: {event}.

- Último intento: `{manifest['attempt_id']}`, modo `{manifest['mode']}`, estado `{manifest['status']}`. [Manifiesto](../../results/{manifest['mode']}/{manifest['attempt_id']}/manifest.json).
- Carga: `{manifest.get('load_container','todavía no iniciada')}`. Datos y volúmenes conservados. Un motor a la vez.
- Definitivas auditadas: {len(records)}/162 requeridas en ampliación; primera vuelta {first}/54. Ejecución parcial mientras falten corridas o tres repeticiones.
- Límite fijo hoy 2026-11-01 22:00 America/Mexico_City; cancelación preventiva desde 21:59 mediante vigilante independiente y control del orquestador.
- Primero 54 escenarios balanceados, luego hasta 18 segundas repeticiones c50 si caben; 60/300/60 y 500k sin reducción.
- Carga y equivalencia completas de ambos motores verificadas en [evidence/full](../../evidence/full/); nueve pruebas locales iniciales de protocolo/horario en [evidencia](../../evidence/smoke/unit-tests-deadline.txt).

Seguimiento: `.venv/bin/python scripts/orchestrate.py status --mode {manifest['mode']}` y `docker inspect --format '{{{{.State.Status}}}} exit={{{{.State.ExitCode}}}}' {manifest.get('load_container','CONTENEDOR')}`. Contrastar heartbeat con runtime real.

Pausa entre corridas: `.venv/bin/python scripts/orchestrate.py stop --mode final`. No detener volúmenes ni eliminar avances. Reanudar matriz con `./scripts/start.ps1 -TaskArguments @('matrix','--mode','final','--project','eva02-jsonbench','--allow-kernel-flush')` solo tras revisar locks/procesos y retirar la señal STOP explícitamente. El horario bloquea corridas que no caben; no se extiende automáticamente a mañana.

El piloto está separado de resultados finales. Configuración congelada y digests en [config](../../config/). [Plan](../PLAN_IMPLEMENTACION.md), [decisiones](DECISIONES.md), [operación](../OPERACION.md). Entregable más reciente: [output/latest.json](../../output/latest.json) cuando exista; se regenera al cerrar el bloque/matriz o al límite, sin trabajo de análisis durante ventanas medidas.
'''
    Path('docs/memoria').mkdir(parents=True, exist_ok=True)
    Path('docs/memoria/ESTADO.md').write_text(text, encoding='utf-8')
    with Path('docs/memoria/BITACORA.md').open('a',encoding='utf-8') as stream:
        stream.write(f"\n- {now}: {event}; {manifest['mode']} {manifest['attempt_id']}; estado={manifest['status']}; evidencia results/{manifest['mode']}/{manifest['attempt_id']}/manifest.json; válidas definitivas {len(records)}, primera vuelta {first}/54.\n")


def engine_id(project, service):
    return compose(project, 'ps', '-q', service)


def cgroup(container_id):
    text = call(['docker', 'exec', container_id, 'sh', '-c',
                 'cat /sys/fs/cgroup/cpu.max; cat /sys/fs/cgroup/memory.max; cat /sys/fs/cgroup/memory.swap.max; cat /sys/fs/cgroup/memory.events'])
    lines = text.splitlines()
    return {'cpu.max': lines[0], 'memory.max': lines[1], 'memory.swap.max': lines[2],
            'memory.events': dict(line.split() for line in lines[3:])}


def source_hash():
    digest = hashlib.sha256()
    sources = sorted([*Path('benchmark_runner').glob('*.py'), *Path('scripts').glob('*.py'),
                      *Path('queries').rglob('*.sql'), *Path('queries').rglob('*.json'),
                      Path('synthetic_data_generator.py'), Path('docker-compose.yml'), Path('requirements.txt'),
                      *Path('config').glob('*.py'), Path('config/matrix.json'), Path('config/prometheus.yml'),
                      Path('config/deadline.json'), Path('config/delivery_schedule.json')])
    for path in sources:
        digest.update(path.as_posix().encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def check_full_dataset():
    dataset = json.loads(Path('data/orders-500000.jsonl.manifest.json').read_text())
    if dataset['orders'] != 500000:
        raise ValueError('Se requieren 500k para piloto/final')
    proofs = []
    for engine, short in [('mongodb', 'mongo'), ('postgresql', 'postgres')]:
        proof = json.loads(Path(f'evidence/full/{short}-load.json').read_text())
        equivalence = json.loads(Path(f'evidence/full/{short}-equivalence.json').read_text())
        if proof.get('verified') is not True or proof['dataset_sha256'] != dataset['sha256'] or proof['orders'] != 500000 or proof['engine'] != engine:
            raise ValueError('Carga completa no verificada para ' + engine)
        if equivalence['engine'] != engine or len(equivalence['validation']) != 2 or not all(v['equivalent_to_reference'] for v in equivalence['validation']):
            raise ValueError('Equivalencia incompleta para ' + engine)
        proofs.append(proof)
    for key in ['canonical_multiset_sha256_sum', 'canonical_multiset_sha256_xor']:
        if proofs[0][key] != proofs[1][key]:
            raise ValueError('Los motores no conservan los mismos documentos')
    return dataset


def receipt_validate(path, project):
    receipt = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if receipt.get('project') != project or receipt.get('verified') is not True:
        raise ValueError('Recibo de flush no verificado para proyecto')
    # No reutilizar una prueba histórica de flush como flush por corrida.
    if time.time() - receipt['epoch'] > 30:
        raise ValueError('Recibo de flush expirado (>30 s); realizar flush antes de warmup de cada corrida')
    return receipt


def execute(cell, mode, project, flush_receipt=None, allow_kernel_flush=False, deadline_admission=None):
    service = 'mongo' if cell['engine'] == 'mongodb' else 'postgres'
    other = 'postgres' if service == 'mongo' else 'mongo'
    if engine_id(project, other):
        raise RuntimeError('Otro motor del experimento activo; detenerlo antes de medir')
    cid = engine_id(project, service)
    if not cid:
        raise RuntimeError('Motor elegido no activo; preparar y verificar dataset antes de medir')
    # Etiqueta guardia: nunca operar recursos ajenos.
    label = call(['docker', 'inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}', cid])
    if label != project:
        raise RuntimeError('Etiqueta de proyecto incorrecta')
    limits = cgroup(cid)
    if mode != 'smoke':
        expected = PROFILES[cell['profile']]
        quota, period = limits['cpu.max'].split()
        if float(quota) / float(period) != expected['cpus'] or int(limits['memory.max']) != int(expected['memory'][:-1]) * 1024**2 or limits['memory.swap.max'] != '0':
            raise RuntimeError('Límites efectivos no coinciden con perfil')
        dataset = check_full_dataset()
        if not flush_receipt and not allow_kernel_flush:
            raise ValueError('Falta flush verificado del kernel real; no se simula cumplimiento')
    stamp = dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S%fZ')
    key = cell['key'] + '-' + stamp
    output = Path('results') / mode / key
    output.mkdir(parents=True, exist_ok=False)
    log = output / 'controller.log'
    manifest = {'key': cell['key'], 'attempt_id': key, 'mode': mode, 'cell': cell,
                'container_id': cid, 'limits_before': limits, 'status': 'preparing',
                'image_lock_sha256': hashlib.sha256(Path('config/images.lock.json').read_bytes()).hexdigest(),
                'source_sha256': source_hash(), 'dataset': dataset if mode != 'smoke' else None}
    manifest['deadline_admission'] = deadline_admission
    manifest['attempt_started_epoch'] = deadline_admission['now_epoch'] if deadline_admission else time.time()
    manifest['engine_image_id'] = call(['docker', 'inspect', '--format', '{{.Image}}', cid])
    manifest['runner_image_id'] = call(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'eva02-jsonbench-runner:local'])
    save(output / 'manifest.json', manifest)
    def runner(*args):
        return compose(project, 'run', '--rm', '-T', 'runner', *args, log=log)
    try:
        runner('-m', 'benchmark_runner.snapshot', '--engine', cell['engine'], '--variant', cell['variant'], '--output', (output / 'engine-before.json').as_posix())
        if mode != 'smoke':
            gate = admission(cell, time.time(), [deadline_admission['observed_max_overhead_seconds']])
            if not gate['allowed']:
                raise TimeoutError('No cabe ciclo completo y evidencia antes de límite horario')
            manifest['flush'] = flush(project, cid) if allow_kernel_flush else receipt_validate(flush_receipt, project)
            save(output / 'flush.json', manifest['flush'])
        raw = Path('results/raw') / mode / key
        # Docker mantiene la carga aunque se cierre el cliente del agente.
        name = 'eva02-load-' + stamp.lower()
        load_id = compose(project, 'run', '-d', '--name', name, 'runner', '-m', 'benchmark_runner.run',
                          '--engine', cell['engine'], '--query-id', cell['query_id'], '--variant', cell['variant'],
                          '--concurrency', str(cell['concurrency']), '--warmup', str(cell['warmup']),
                          '--measure', str(cell['measure']), '--cooldown', str(cell['cooldown']), '--output', raw.as_posix(), log=log).splitlines()[-1]
        manifest.update(status='running', load_container=load_id, raw=raw.as_posix(), started_epoch=time.time())
        engine_parent = call(['docker', 'inspect', '--format', '{{.HostConfig.CgroupParent}}', cid])
        load_parent = call(['docker', 'inspect', '--format', '{{.HostConfig.CgroupParent}}', load_id])
        # IDs distintos y padres Docker iguales -> cgroups hermanos; guardar cgroup real desde cada contenedor.
        manifest['isolation'] = {
            'engine_cgroup': call(['docker', 'exec', cid, 'cat', '/proc/self/cgroup']),
            'load_cgroup': call(['docker', 'exec', load_id, 'cat', '/proc/self/cgroup']),
            'engine_cgroup_parent': engine_parent, 'load_cgroup_parent': load_parent,
            'engine_id': cid, 'load_id': load_id,
            'note': 'Namespaces privados muestran 0::/; identidad real cgroup bajo /sys/fs/cgroup/docker/<id> validada por cAdvisor y límites'}
        save(output / 'manifest.json', manifest)
        progress_update(manifest, 'inicio de carga experimental')
        if CONTROLLER:
            CONTROLLER.update(phase='running', attempt_id=key, key=cell['key'])
        while call(['docker', 'inspect', '--format', '{{.State.Running}}', load_id]) == 'true':
            if time.time() >= policy()['load_cutoff_epoch']:
                stop_project_load(project, output / 'deadline-cancellation')
                raise TimeoutError('Corrida incompleta: cancelación por límite horario 22:00 America/Mexico_City')
            manifest['heartbeat_epoch'] = time.time()
            save(output / 'manifest.json', manifest)
            time.sleep(2)
        call(['docker', 'logs', load_id], log=log)
        code = call(['docker', 'inspect', '--format', '{{.State.ExitCode}}', load_id])
        if code != '0':
            raise RuntimeError('Carga terminó con error; conservar intento')
        summary = json.loads((raw / 'summary.json').read_text())
        window = json.loads((raw / 'window.json').read_text())
        start = window['anchor']['epoch'] + window['start'] - window['anchor']['monotonic']
        end = start + cell['measure']
        try:
            telemetry = collect(cid, start, end)
        except Exception as telemetry_error:
            telemetry = {'start_epoch': start, 'end_epoch': end, 'container_id': cid,
                         'metrics': {'cpu': None, 'cfs': None, 'rss': None}, 'raw': {},
                         'limitations': ['Transporte telemetría: '+str(telemetry_error)]}
        save(output / 'telemetry.json', telemetry)
        manifest['limits_after'] = cgroup(cid)
        runner('-m', 'benchmark_runner.snapshot', '--engine', cell['engine'], '--variant', cell['variant'], '--output', (output / 'engine-after.json').as_posix())
        call(['docker', 'logs', '--since', str(int(manifest['started_epoch'])), cid], log=output / 'engine.log')
        before = json.loads((output / 'engine-before.json').read_text())
        after = json.loads((output / 'engine-after.json').read_text())
        oom = int(manifest['limits_after']['memory.events']['oom_kill']) > int(limits['memory.events']['oom_kill'])
        spill = None
        if cell['engine'] == 'postgresql':
            # Dedicated DB, plans must remain outside this before/after interval.
            if before['temp'][0][2] == after['temp'][0][2]:
                spill = after['temp'][0][1] > before['temp'][0][1]
        else:
            # Mongo release-specific counters: no suponer un nombre ausente.
            a = before.get('metrics', {}).get('query', {}).get('sort', {})
            b = after.get('metrics', {}).get('query', {}).get('sort', {})
            ga = before.get('metrics', {}).get('query', {}).get('group', {})
            gb = after.get('metrics', {}).get('query', {}).get('group', {})
            if 'spillToDisk' in a and 'spillToDisk' in b and 'spills' in ga and 'spills' in gb:
                spill = b['spillToDisk'] > a['spillToDisk'] or gb['spills'] > ga['spills']
        metrics = telemetry['metrics']
        missing = any(metrics.get(k) is None for k in ['rss', 'cpu', 'cfs']) or spill is None
        status = 'oom' if oom else 'error' if summary['errors_total'] or missing else 'spill' if spill else 'ok'
        row = dict.fromkeys(HEADER)
        row.update({k: cell[k] for k in ['engine', 'profile', 'query_id', 'concurrency', 'run_number']})
        row.update({k: summary[k] for k in ['throughput_qps', 'latency_p50_ms', 'latency_p95_ms', 'latency_p99_ms']})
        row.update(timestamp_epoch=start, cpu_throttled_seconds=metrics.get('cfs'),
                   max_rss_mb=metrics['rss']/1e6 if metrics.get('rss') is not None else None,
                   disk_spill=None if spill is None else str(spill).lower(), status=status)
        write_csv(output / 'metrics.csv', row)
        # Un error de consulta es un resultado experimental si el ciclo y evidencia están completos.
        # Una ausencia de telemetría o caída del protocolo sigue invalidando el intento.
        manifest.update(status='invalid' if missing else 'complete',
                        csv_status=status, summary=summary, cpu_usage_seconds=metrics.get('cpu'),
                        telemetry_limitations=telemetry['limitations'], spill_observed=spill, oom_killed=oom,
                        finished_epoch=time.time())
        manifest['observed_overhead_seconds'] = max(0, manifest['finished_epoch']-manifest['attempt_started_epoch']-sum(cell[k] for k in ['warmup','measure','cooldown']))
        manifest['spill_evidence_scope'] = 'delta engine-before -> engine-after; incluye warmup/medición/drenaje, no solo ventana'
        manifest['percentile_reliability_note'] = 'menos de 100 muestras; P99 interpolado tiene poca resolución' if summary['latency_samples'] < 100 else None
        if mode != 'smoke':
            denom = dataset['orders'] * summary['completed_in_window'] / 1000
            manifest['M3_nominal'] = {
                'cpu_seconds_per_1000_dataset_orders_per_completed_query': metrics['cpu']/denom if metrics.get('cpu') is not None and denom else None,
                'rss_bytes_per_1000_dataset_orders': metrics['rss']/(dataset['orders']/1000) if metrics.get('rss') is not None else None,
                'limitation': 'Normalización nominal por órdenes del dataset, no documentos examinados observados; CPU incluye trabajo de cruces'}
        save(output / 'manifest.json', manifest)
        progress_update(manifest, 'corrida terminada, pendiente auditoría')
        print(output, status, flush=True)
        return manifest
    except BaseException as error:
        manifest.update(status='interrupted', error=str(error), finished_epoch=time.time())
        manifest['incomplete'] = True
        if time.time() >= policy()['load_cutoff_epoch'] or isinstance(error, TimeoutError):
            manifest['failure_kind'] = 'deadline_or_insufficient_time'
        try:
            state = json.loads(call(['docker', 'inspect', '--format', '{{json .State}}', cid]))
            # Conservar solo estado runtime, nunca inspección completa con variables secretas.
            manifest['engine_failure_state'] = state
            if state.get('OOMKilled') is True:
                manifest['oom_killed'] = True
        except Exception as inspect_error:
            manifest['failure_state_unavailable'] = str(inspect_error)
        # Una caída previa a métricas sigue teniendo fila de fallo con ausencias explícitas.
        if not (output / 'metrics.csv').exists():
            row = dict.fromkeys(HEADER)
            row.update({k: cell[k] for k in ['engine', 'profile', 'query_id', 'concurrency', 'run_number']})
            row.update(timestamp_epoch=manifest.get('started_epoch', time.time()), status='oom' if manifest.get('oom_killed') else 'error')
            write_csv(output / 'metrics.csv', row)
        save(output / 'manifest.json', manifest)
        progress_update(manifest, 'corrida incompleta; revisar evidencia antes de continuar')
        raise


def main():
    os.chdir(ROOT)
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['run', 'status', 'stop', 'dry-run', 'matrix'])
    p.add_argument('--mode', choices=['smoke', 'pilot', 'final'], default='smoke')
    p.add_argument('--project', default='eva02-jsonbench-smoke')
    p.add_argument('--key')
    p.add_argument('--flush-receipt')
    p.add_argument('--allow-kernel-flush', action='store_true')
    p.add_argument('--limit', type=int)
    p.add_argument('--seconds', type=int, default=5)
    args = p.parse_args()
    root = Path('results') / args.mode
    root.mkdir(parents=True, exist_ok=True)
    if args.action == 'stop':
        (root / 'STOP_BETWEEN_RUNS').touch()
        print('Parada solicitada entre corridas; no mata solicitudes ni borra evidencia.')
        return
    if args.action == 'status':
        state_file = root / 'controller-state.json'
        if state_file.exists():
            state = json.loads(state_file.read_text())
            print('controller_state', json.dumps(state),
                  'pid_alive=', pid_alive(state['pid']),
                  'heartbeat_age_seconds=', round(time.time()-state['heartbeat_epoch'], 2))
        if (root / 'controller.lock').exists():
            print('controller_lock', (root / 'controller.lock').read_text())
        for path in sorted(root.glob('*/manifest.json')):
            m = json.loads(path.read_text())
            runtime = 'sin contenedor registrado'
            if m.get('load_container'):
                try:
                    runtime = call(['docker', 'inspect', '--format', '{{.State.Status}} exit={{.State.ExitCode}}', m['load_container']])
                except Exception as error:
                    runtime = str(error)
            print(m.get('attempt_id'), m.get('status'), m.get('heartbeat_epoch'), runtime)
        return
    if args.action == 'dry-run':
        heartbeat = root / f'persistence-check-{os.getpid()}.json'
        for _ in range(args.seconds):
            save(heartbeat, {'pid': os.getpid(), 'heartbeat_epoch': time.time(), 'state': 'running'})
            time.sleep(1)
        save(heartbeat, {'pid': os.getpid(), 'heartbeat_epoch': time.time(), 'state': 'complete'})
        return
    if args.action == 'matrix':
        if args.mode != 'final' or not args.allow_kernel_flush:
            p.error('Matriz requiere --mode final --allow-kernel-flush y piloto aceptado')
        gate = json.loads(Path('config/acceptance.json').read_text())
        if gate.get('pilot_passed') is not True:
            raise RuntimeError('Piloto no aprobado')
        expected_lock = hashlib.sha256(Path('config/images.lock.json').read_bytes()).hexdigest()
        if gate['image_lock_sha256'] != expected_lock:
            raise RuntimeError('Lock cambió desde aceptación')
        if gate['source_sha256'] != source_hash():
            raise RuntimeError('Implementación cambió desde aceptación; revisar/revalidar antes de matriz')
        selected = delivery_schedule()
    else:
        selected = [c for c in matrix() if c['key'] == args.key]
    if not selected:
        p.error('--key debe identificar una celda de config/matrix.json')
    lock = root / 'controller.lock'
    try:
        with lock.open('x') as stream:
            json.dump({'pid': os.getpid(), 'epoch': time.time(), 'key': args.key}, stream)
    except FileExistsError:
        raise RuntimeError('Lock existente: comprobar PID/contenedor antes de reanudar; no eliminar a ciegas')
    global CONTROLLER
    CONTROLLER = ControllerState(root/'controller-state.json', save)
    terminal_phase = 'finished'
    try:
        executed = 0
        for cell in selected:
            if (root / 'STOP_BETWEEN_RUNS').exists():
                terminal_phase = 'paused_between_runs'
                print('Parada entre corridas activa; punto de reanudación conservado.')
                break
            if args.action == 'matrix' and source_hash() != gate['source_sha256']:
                raise RuntimeError('Código/configuración cambió durante matriz; detener y conservar evidencia')
            completed = []
            for previous_path in root.glob('*/manifest.json'):
                previous = json.loads(previous_path.read_text())
                if args.mode != 'smoke' and previous.get('status') == 'complete':
                    audit_file = previous_path.parent/'audit.json'
                    if not audit_file.exists():
                        from audit_run import audit
                        audit(previous_path.parent)
                    if not json.loads(audit_file.read_text()).get('passed'):
                        raise RuntimeError('Corrida previa no auditada; no repetir ni asumir validez')
                completed.append(previous)
            if any(m.get('key') == cell['key'] and m.get('status') == 'complete' for m in completed):
                print('Corrida ya completa; se conserva y no se repite.', flush=True)
                continue
            if any(m.get('key') == cell['key'] and m.get('status') == 'running' for m in completed):
                raise RuntimeError('Intento running previo: revisar contenedor/evidencia antes de reanudar')
            if cell.get('delivery_stage') == 'optional_second_c50':
                first_keys = {c['key'] for c in delivery_schedule() if c['delivery_stage'] == 'first_pass'}
                done = {m['key'] for m in completed if m['status'] == 'complete'}
                if not first_keys <= done:
                    terminal_phase = 'first_pass_incomplete'
                    break
            def run_once():
                nonlocal executed
                if (root/'STOP_BETWEEN_RUNS').exists():
                    raise RuntimeError('Parada entre corridas solicitada')
                if args.action == 'matrix' and source_hash() != gate['source_sha256']:
                    raise RuntimeError('Fuente cambió antes del reintento')
                observed = [m['observed_overhead_seconds'] for m in completed if m.get('status') == 'complete' and 'observed_overhead_seconds' in m]
                decision = admission(cell, time.time(), observed)
                if not decision['allowed']:
                    save(root / ('deadline-stop-' + str(int(time.time())) + '.json'), decision)
                    print('No cabe otra corrida completa con margen; se conserva cobertura parcial.', flush=True)
                    raise TimeoutError('No cabe otra corrida antes del corte')
                if args.mode != 'smoke':
                    guard_pointer = json.loads(Path('evidence/deadline/20261101/current.json').read_text(encoding='utf-8-sig'))
                    guard_path = Path(guard_pointer['directory'])
                    guard = json.loads((guard_path / 'process.json').read_text())
                    heartbeat = json.loads((guard_path / 'heartbeat.json').read_text())
                    if guard['project'] != args.project or guard['hard_epoch'] != decision['hard_epoch'] or time.time()-heartbeat['epoch'] > 5:
                        raise RuntimeError('Falta vigilante independiente activo con límite correcto')
                if args.mode == 'smoke':
                    cell.update(warmup=2, measure=5, cooldown=2, profile='SMOKE_INFRA')
                if args.action == 'matrix':
                    service = 'mongo' if cell['engine'] == 'mongodb' else 'postgres'
                    other = 'postgres' if service == 'mongo' else 'mongo'
                    compose(args.project, 'stop', other)
                    env = dict(os.environ, ENGINE_CPUS=str(PROFILES[cell['profile']]['cpus']), ENGINE_MEMORY=PROFILES[cell['profile']]['memory'])
                    compose(args.project, 'up', '-d', '--wait', service, env=env)
                result = execute(cell, args.mode, args.project, args.flush_receipt, args.allow_kernel_flush, decision)
                executed += 1
                if result['status'] != 'complete':
                    return result
                if args.mode in ('pilot', 'final'):
                    from audit_run import audit
                    audit_path = Path('results') / args.mode / result['attempt_id']
                    try:
                        audit(audit_path)
                    except BaseException as audit_error:
                        result.update(status='invalid', audit_error=str(audit_error))
                        save(audit_path/'manifest.json',result)
                        progress_update(result,'auditoría fallida')
                        raise
                    progress_update(result,'auditoría aprobada')
                return result

            def incident(current, state, result):
                directory = root/'incidents'
                directory.mkdir(exist_ok=True)
                record = {'key': current['key'], 'state': state, 'epoch': time.time(),
                          'attempt_id': result['attempt_id'], 'max_telemetry_retries': 1}
                save(directory/(current['key']+'.json'), record)
                if state != 'resolved':
                    progress_update(result, 'incidente telemetría: '+state)

            def recover(result):
                CONTROLLER.update(phase='recovering_telemetry', key=cell['key'], attempt_id=result['attempt_id'])
                directory = root/'recovery'/result['attempt_id']
                directory.mkdir(parents=True, exist_ok=True)
                def probe_save(data):
                    save(directory/(str(time.time_ns())+'.json'), data)
                try:
                    service = 'mongo' if cell['engine'] == 'mongodb' else 'postgres'
                    other = 'postgres' if service == 'mongo' else 'mongo'
                    compose(args.project, 'stop', other)
                    env = dict(os.environ, ENGINE_CPUS=str(PROFILES[cell['profile']]['cpus']), ENGINE_MEMORY=PROFILES[cell['profile']]['memory'])
                    compose(args.project, 'up', '-d', '--wait', service, env=env)
                    cid = engine_id(args.project, service)
                    wait_healthy(cid, probe_save, lambda: (root/'STOP_BETWEEN_RUNS').exists())
                except Exception as error:
                    stop_project_load(args.project, directory/'blocked-stop')
                    save(root/'BLOCKED.json', {'epoch': time.time(), 'key': cell['key'], 'reason': str(error),
                                              'evidence': directory.as_posix()})
                    raise
                CONTROLLER.update(phase='between_runs', key=cell['key'])

            CONTROLLER.update(phase='preparing', key=cell['key'])
            if args.action == 'matrix':
                bounded(cell, completed, run_once, recover, incident)
            else:
                result = run_once()
                if result['status'] != 'complete':
                    raise RuntimeError('Intento inválido; revisar evidencia')
            CONTROLLER.update(phase='between_runs', key=cell['key'])
            if args.mode == 'final' and executed and executed % 9 == 0:
                call([sys.executable, 'scripts/backup.py'])
            if args.limit and executed >= args.limit:
                break
    except BaseException as error:
        terminal_phase = 'deadline_stop' if isinstance(error, TimeoutError) else 'blocked'
        CONTROLLER.update(error=str(error))
        if args.action == 'matrix':
            stop_project_load(args.project, root/('controller-stop-'+str(time.time_ns())))
        blocked = root/'BLOCKED.json'
        details = json.loads(blocked.read_text()) if blocked.exists() else {}
        save(blocked, dict(details, epoch=time.time(), reason=str(error), state=terminal_phase))
        raise
    finally:
        CONTROLLER.close(terminal_phase)
        lock.unlink()
        if args.action == 'matrix':
            # Fuera de ventanas de medición: generar tablas/gráficas y paquete versionado.
            call([sys.executable, 'scripts/report.py'], log=Path('results/final/consolidation.log'), check=False)


if __name__ == '__main__':
    main()
