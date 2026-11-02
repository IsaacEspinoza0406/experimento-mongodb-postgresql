"""Matriz explícita y orden balanceado por repetición; no inicia mediciones."""
import hashlib
import json

PROFILES = {'PROFILE_A_BASELINE': {'cpus': 4, 'memory': '4096m'},
            'PROFILE_B_MEDIUM': {'cpus': 2, 'memory': '2048m'},
            'PROFILE_C_SEVERE': {'cpus': 1, 'memory': '1024m'}}


def matrix():
    rows = []
    for repetition in (1, 2, 3):
        # Alternancia del orden motor entre repeticiones y perfiles.
        for pidx, profile in enumerate(PROFILES):
            engines = ('mongodb', 'postgresql') if (pidx + repetition) % 2 else ('postgresql', 'mongodb')
            for engine in engines:
                for query_id in ('Q1', 'Q2'):
                    variants = ('default',) if query_id == 'Q1' else (('nodisk', 'disk') if engine == 'mongodb' else ('4MB', '64MB'))
                    for variant in variants:
                        for concurrency in (1, 10, 50):
                            key = f'{engine}-{profile}-{query_id}-{variant}-c{concurrency}-r{repetition}'
                            rows.append({'key': key, 'engine': engine, 'profile': profile,
                                         'query_id': query_id, 'variant': variant,
                                         'concurrency': concurrency, 'run_number': repetition,
                                         'warmup': 60, 'measure': 300, 'cooldown': 60})
    return rows


if __name__ == '__main__':
    from pathlib import Path
    p = Path('config/matrix.json')
    with p.open('x') as out:
        json.dump({'proposed_extension': True, 'runs': matrix()}, out, indent=2)
    print(f'{len(matrix())} corridas; mínimo {len(matrix()) * 420 / 3600:.2f} horas sin overhead')
