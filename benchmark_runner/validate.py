import argparse
import json
import heapq
import hashlib
from pathlib import Path
from .db import connect, prepare, query
from .semantics import q1_reference, q1_compare, q2_compare, cents


def validate(engine, dataset, load_evidence):
    proof = json.loads(Path(load_evidence).read_text())
    manifest = json.loads(Path(dataset + '.manifest.json').read_text())
    if proof.get('verified') is not True or proof['engine'] != engine or proof['orders'] != manifest['orders'] or proof['dataset_sha256'] != manifest['sha256']:
        raise ValueError('Se exige carga completa/verificada del mismo dataset y motor')
    def documents():
        with Path(dataset).open() as stream:
            yield from (json.loads(line) for line in stream)
    expected_q1 = q1_reference(documents())
    # Dos pasadas streaming: conservar solo candidatos al Top-K, incluidos empates.
    top = []
    for order in documents():
        if order['payment']['status'] == 'completed' and order['timestamp'] >= '2026-01-01T00:00:00Z':
            heapq.heappush(top, cents(order['payment']['final_total']))
            if len(top) > 1000:
                heapq.heappop(top)
    cutoff = min(top) if top else 0
    orders = [o for o in documents() if o['payment']['status'] == 'completed'
              and o['timestamp'] >= '2026-01-01T00:00:00Z' and cents(o['payment']['final_total']) >= cutoff]
    connection = connect(engine)
    evidence = []
    for variant in (('nodisk', 'disk') if engine == 'mongodb' else ('4MB', '64MB')):
        prepare(engine, connection, variant)
        q1 = query(engine, connection, 'Q1', variant)
        mapped = ({(r['_id']['country'], r['_id']['category']): (r['total_revenue'], r['avg_revenue'], r['count']) for r in q1}
                  if engine == 'mongodb' else {(r[0], r[1]): r[2:] for r in q1})
        q1_compare(expected_q1, mapped)
        q2 = query(engine, connection, 'Q2', variant)
        rows = ([(r['order_id'], r['customer']['id'], r['payment']['final_total']) for r in q2]
                if engine == 'mongodb' else q2)
        q2_compare(orders, rows)
        evidence.append({'variant': variant, 'Q1_groups': len(q1), 'Q2_rows': len(rows), 'equivalent_to_reference': True})
    connection.close()
    return {'engine': engine, 'dataset': dataset, 'validation': evidence}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--engine', choices=['mongodb', 'postgresql'], required=True)
    p.add_argument('--dataset', required=True)
    p.add_argument('--evidence', required=True)
    p.add_argument('--load-evidence', required=True)
    a = p.parse_args()
    result = validate(a.engine, a.dataset, a.load_evidence)
    with Path(a.evidence).open('x') as output:
        json.dump(result, output, indent=2)
    print(json.dumps(result))
