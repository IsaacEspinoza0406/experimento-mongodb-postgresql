"""Flush explícito en kernel WSL del daemon, guardado por proyecto y boot_id."""
import json
import subprocess
import time


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(f'Falló {args[0]}: {result.stderr or result.stdout}')
    return result.stdout.strip().replace('\x00', '')


def flush(project, container_id):
    running = command(['docker', 'ps', '--format', '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.service"}}'])
    foreign = [r for r in running.splitlines() if r.split('|')[1] != project]
    loaders = [r for r in running.splitlines() if r.split('|')[2] == 'runner']
    if foreign or loaders:
        raise RuntimeError(f'No flush con contenedores ajenos o carga activa: {foreign + loaders}')
    linux_boot = command(['wsl', '-d', 'docker-desktop', '--', 'cat', '/proc/sys/kernel/random/boot_id'])
    engine_boot = command(['docker', 'exec', container_id, 'cat', '/proc/sys/kernel/random/boot_id'])
    if linux_boot != engine_boot:
        raise RuntimeError('WSL y motor no comparten boot_id/kernel; no hacer flush')
    before = command(['wsl', '-d', 'docker-desktop', '--', 'cat', '/proc/meminfo'])
    output = command(['wsl', '-d', 'docker-desktop', '--', 'sh', '-c',
                      'sync && echo 3 > /proc/sys/vm/drop_caches && echo flush_written=3'])
    after = command(['wsl', '-d', 'docker-desktop', '--', 'cat', '/proc/meminfo'])
    if output != 'flush_written=3':
        raise RuntimeError('No se confirmó escritura drop_caches')
    return {'project': project, 'epoch': time.time(), 'verified': True, 'boot_id': linux_boot,
            'method': 'WSL docker-desktop: sync; echo 3 > /proc/sys/vm/drop_caches',
            'output': output, 'memory_before': before, 'memory_after': after,
            'limitation': 'Flush de page cache/dentries/inodes Linux; no caches internas de motor ni Windows'}


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    p = argparse.ArgumentParser()
    p.add_argument('--project', required=True)
    p.add_argument('--container-id', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    result = flush(a.project, a.container_id)
    with Path(a.output).open('x') as out:
        json.dump(result, out, indent=2)
    print(json.dumps({k: result[k] for k in ['project', 'epoch', 'verified', 'boot_id', 'output']}))
