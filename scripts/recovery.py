"""Recuperación limitada entre ciclos; nunca modifica evidencia o validez."""
import ctypes
import ast
import json
import os
import threading
import time
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_runner.telemetry import collect
from deadline_policy import policy


def recorded_engine_outcomes(m):
    """Solo timeouts PG comprobados; un fallo de conexión no es un resultado equivalente."""
    count = m.get('summary', {}).get('errors_total')
    if count == 0:
        return True
    if not isinstance(count, int) or count < 1 or m.get('cell', {}).get('engine') != 'postgresql':
        return False
    try:
        files = list(Path(m['raw']).glob('worker-*.jsonl'))
        if len(files) != m['cell']['concurrency']:
            return False
        errors = [json.loads(line)['error'] for f in files for line in f.read_text().splitlines()
                  if not json.loads(line)['ok']]
        if len(errors) != count:
            return False
        for error in errors:
            if error['type'] != 'DatabaseError':
                return False
            detail = ast.literal_eval(error['message'])
            if detail.get('C') != '57014' or detail.get('M') != 'canceling statement due to statement timeout':
                return False
        return True
    except (OSError, KeyError, ValueError, SyntaxError, TypeError, AttributeError):
        return False


def telemetry_only(m):
    return (m.get('status') == 'invalid' and bool(m.get('telemetry_limitations'))
            and m.get('spill_observed') is not None and not m.get('audit_error')
            and not m.get('incomplete') and not m.get('oom_killed')
            and recorded_engine_outcomes(m))


def bounded(cell, records, run_once, recover, incident):
    """Hasta dos intentos totales por clave, incluso tras reiniciar controlador."""
    prior = [m for m in records if m.get('key') == cell['key']]
    if any(m.get('status') == 'complete' for m in prior):
        return None
    failures = [m for m in prior if telemetry_only(m)]
    if any(m.get('status') in ('invalid', 'interrupted') and not telemetry_only(m) for m in prior):
        raise RuntimeError('Fallo previo no recuperable automáticamente; revisar evidencia')
    if failures:
        incident(cell, 'pending_incident' if len(failures) >= 2 else 'checking_recovery', failures[-1])
        recover(failures[-1])
    if len(failures) >= 2:
        return None
    while True:
        result = run_once()
        if result['status'] == 'complete':
            incident(cell, 'resolved', result)
            return result
        if not telemetry_only(result):
            raise RuntimeError('Intento inválido no atribuible exclusivamente a telemetría')
        failures.append(result)
        incident(cell, 'pending_incident' if len(failures) >= 2 else 'checking_recovery', result)
        recover(result)
        if len(failures) >= 2:
            return None


def healthy_probe(container_id):
    now = time.time()
    with urllib.request.urlopen('http://127.0.0.1:19090/api/v1/targets', timeout=10) as response:
        targets = json.load(response)
    rows = targets['data']['activeTargets']
    jobs = {r['labels'].get('job'): r for r in rows}
    for job in ('cadvisor', 'node'):
        if job not in jobs or jobs[job]['health'] != 'up' or jobs[job]['lastError']:
            raise RuntimeError('Colector no sano: ' + job)
    data = collect(container_id, now-62, now-2)
    if data['limitations']:
        raise RuntimeError('; '.join(data['limitations']))
    # Node también debe tener muestras recientes, no basta un health histórico.
    for metric in ('node_memory_MemAvailable_bytes', 'node_memory_SwapFree_bytes'):
        series = data['raw'][metric]['data']['result']
        if not series or any(now-float(r['values'][-1][0]) > 5 for r in series):
            raise RuntimeError('Node sin muestras recientes')
        for row in series:
            stamps = [float(v[0]) for v in row['values']]
            if max((b-a for a,b in zip(stamps,stamps[1:])), default=0) > 2.5:
                raise RuntimeError('Node con huecos en ventana de recuperación')
    return {'epoch': now, 'targets': targets, 'telemetry': data}


def wait_healthy(container_id, save_probe, stopped, clock=time.monotonic,
                 sleep=time.sleep, probe=None, timeout=180):
    started = clock()
    if probe is None:
        def probe(cid):
            # Aislar también el timeout agregado de las peticiones HTTP.
            remaining = max(0.1, min(20, timeout-(clock()-started)))
            process = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), cid],
                                     capture_output=True, text=True, encoding='utf-8', timeout=remaining)
            if process.returncode:
                raise RuntimeError(process.stderr[-2000:])
            return json.loads(process.stdout)
    while True:
        if stopped() or time.time() >= policy()['load_cutoff_epoch']:
            raise RuntimeError('Recuperación cancelada por pausa o corte horario')
        try:
            result = probe(container_id)
        except Exception as error:
            save_probe({'epoch': time.time(), 'healthy': False, 'error': str(error)})
        else:
            save_probe({'epoch': time.time(), 'healthy': True, 'evidence': result})
            return
        if clock()-started >= timeout:
            raise RuntimeError('Colectores no recuperados en 180s; carga bloqueada')
        sleep(min(10, max(0, timeout-(clock()-started))))


def pid_alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False


class ControllerState:
    def __init__(self, path, save):
        self.path, self.save = path, save
        self.data = {'pid': os.getpid(), 'phase': 'starting', 'started_epoch': time.time()}
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def update(self, **fields):
        with self.lock:
            self.data.update(fields, progress_epoch=time.time())

    def _loop(self):
        while True:
            with self.lock:
                self.save(self.path, dict(self.data, heartbeat_epoch=time.time()))
            if self.done.wait(2):
                return

    def close(self, phase, **fields):
        self.done.set()
        self.thread.join()
        with self.lock:
            self.data.update(fields, phase=phase, heartbeat_epoch=time.time(), finished_epoch=time.time())
            self.save(self.path, self.data)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    print(json.dumps(healthy_probe(sys.argv[1])))
