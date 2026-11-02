import math
from decimal import Decimal


def q1_reference(orders):
    result = {}
    for order in orders:
        for item in order['items']:
            if Decimal(str(item['price'])) <= 50:
                continue
            key = (order['customer']['geo']['country'], item['category'])
            total, count = result.get(key, (Decimal(0), 0))
            result[key] = (total + Decimal(str(item['price'])) * item['qty'], count + 1)
    return {key: (total, total / count, count) for key, (total, count) in result.items()}


def q1_compare(expected, actual):
    assert set(expected) == set(actual), 'Q1: claves diferentes'
    for key, (total, average, count) in expected.items():
        got = actual[key]
        assert got[2] == count, f'Q1 count {key}'
        for a, b in zip((total, average), got[:2]):
            assert math.isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-6), f'Q1 dinero {key}'


def cents(value):
    return int((Decimal(str(value)) * 100).to_integral_value())


def q2_compare(orders, rows, k=1000):
    eligible = {o['order_id']: o for o in orders if o['payment']['status'] == 'completed'
                and o['timestamp'] >= '2026-01-01T00:00:00Z'}
    totals = sorted((cents(o['payment']['final_total']) for o in eligible.values()), reverse=True)
    assert len(rows) == min(k, len(eligible)), 'Q2 cardinalidad'
    ids = [r[0] for r in rows]
    assert len(ids) == len(set(ids)), 'Q2 IDs duplicados'
    if not totals:
        return
    cutoff = totals[min(k, len(totals)) - 1]
    required = {oid for oid, o in eligible.items() if cents(o['payment']['final_total']) > cutoff}
    assert required <= set(ids), 'Q2 omitió un valor superior al corte'
    previous = float('inf')
    for oid, customer_id, total in rows:
        assert oid in eligible, 'Q2 orden no elegible'
        order = eligible[oid]
        assert customer_id == order['customer']['id'], 'Q2 customer diferente'
        assert cents(total) == cents(order['payment']['final_total']) >= cutoff, 'Q2 total/corte'
        assert cents(total) <= previous, 'Q2 orden incorrecto'
        previous = cents(total)
