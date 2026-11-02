"""Primero 54 escenarios; después solo rep2/c50. Orden balanceado en ondas de seis."""
from .matrix import matrix


def schedule():
    pairs = [('postgresql', 'PROFILE_A_BASELINE'), ('mongodb', 'PROFILE_B_MEDIUM'),
             ('postgresql', 'PROFILE_C_SEVERE'), ('mongodb', 'PROFILE_A_BASELINE'),
             ('postgresql', 'PROFILE_B_MEDIUM'), ('mongodb', 'PROFILE_C_SEVERE')]
    workloads = [('Q1', None, 1), ('Q2', 0, 10), ('Q2', 1, 50),
                 ('Q1', None, 10), ('Q2', 0, 50), ('Q2', 1, 1),
                 ('Q1', None, 50), ('Q2', 0, 1), ('Q2', 1, 10)]
    lookup = {c['key']: c for c in matrix()}
    result = []
    for repetition, wave_list in [(1, workloads), (2, [('Q1', None, 50), ('Q2', 0, 50), ('Q2', 1, 50)])]:
        for wave, (query, variant_index, concurrency) in enumerate(wave_list):
            rotated = pairs[wave % 6:] + pairs[:wave % 6]
            for engine, profile in rotated:
                variants = ('nodisk', 'disk') if engine == 'mongodb' else ('4MB', '64MB')
                variant = 'default' if query == 'Q1' else variants[variant_index]
                key = f'{engine}-{profile}-{query}-{variant}-c{concurrency}-r{repetition}'
                cell = dict(lookup[key])
                cell['delivery_stage'] = 'first_pass' if repetition == 1 else 'optional_second_c50'
                result.append(cell)
    return result


if __name__ == '__main__':
    import json
    from pathlib import Path
    with Path('config/delivery_schedule.json').open('x') as output:
        json.dump(schedule(), output, indent=2)
    print('54 escenarios iniciales + hasta 18 repeticiones c50, sin alterar matriz académica de referencia')
