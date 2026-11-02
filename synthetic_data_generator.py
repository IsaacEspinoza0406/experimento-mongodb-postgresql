"""Órdenes sintéticas deterministas. Solo biblioteca estándar; nunca sobrescribe datasets."""
import argparse
import datetime as dt
import hashlib
import json
import random
import uuid
from pathlib import Path

VERSION = 1
COUNTRIES = ('MX', 'US', 'CA', 'BR', 'ES')
CATEGORIES = ('electronics', 'books', 'home', 'sports', 'clothing')


def generate(count=500_000, seed=243751):
    rng = random.Random(seed)
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, f'orders-benchmark-v{VERSION}:{seed}')
    epoch = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    for n in range(count):
        order_id = str(uuid.uuid5(namespace, f'order:{n}'))
        items = []
        # 1..5 uniforme: esperanza 3; no se ajusta la consulta/dataset para forzar spill.
        for _ in range(rng.randint(1, 5)):
            items.append({'sku': f'SKU-{rng.randrange(10_000):05d}', 'category': rng.choice(CATEGORIES),
                          'brand': f'brand-{rng.randrange(20)}', 'qty': rng.randint(1, 5),
                          'price': rng.randint(100, 50_000) / 100, 'tax_pct': 16,
                          'discounts': []})
        subtotal_cents = sum(i['qty'] * round(i['price'] * 100) for i in items)
        tax_cents = (subtotal_cents * 16 + 50) // 100
        final = (subtotal_cents + tax_cents) / 100
        timestamp = (epoch + dt.timedelta(seconds=rng.randrange(730 * 86400))).strftime('%Y-%m-%dT%H:%M:%SZ')
        yield {
            'order_id': order_id, 'timestamp': timestamp,
            'customer': {'id': str(uuid.uuid5(namespace, f'customer:{rng.randrange(50_000)}')),
                         'tier': rng.choice(('basic', 'silver', 'gold')),
                         'segment': rng.choice(('retail', 'business')),
                         'geo': {'country': rng.choice(COUNTRIES), 'state': 'synthetic-state',
                                 'city': 'synthetic-city', 'zip': f'{rng.randrange(100_000):05d}',
                                 'coordinates': {'lat': round(rng.uniform(-60, 60), 5),
                                                 'lon': round(rng.uniform(-150, 150), 5)}}},
            'items': items,
            'payment': {'method': rng.choice(('card', 'transfer', 'wallet')),
                        'status': rng.choices(('completed', 'pending', 'failed'), (8, 1, 1))[0],
                        'transaction_id': str(uuid.uuid5(namespace, f'transaction:{n}')),
                        'gateway_latency_ms': rng.randint(10, 500),
                        'totals': {'subtotal': subtotal_cents / 100, 'tax': tax_cents / 100,
                                   'discount': 0, 'final_total': final}, 'final_total': final},
            'metadata': {'channel': rng.choice(('web', 'mobile', 'store')),
                         'user_agent': 'synthetic-benchmark/1',
                         'session_id': str(uuid.uuid5(namespace, f'session:{n}')), 'flags': []}}


def write_dataset(path, count, seed):
    path = Path(path)
    manifest_path = path.with_suffix(path.suffix + '.manifest.json')
    if path.exists() or manifest_path.exists() or path.with_suffix(path.suffix + '.partial').exists():
        raise FileExistsError(f'Dataset o intento parcial existente: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + '.partial')
    digest = hashlib.sha256()
    item_count = 0
    with partial.open('xb') as output:
        for order in generate(count, seed):
            data = (json.dumps(order, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode()
            digest.update(data)
            item_count += len(order['items'])
            output.write(data)
    partial.rename(path)
    manifest = {'generator_version': VERSION, 'seed': seed, 'orders': count, 'items': item_count,
                'mean_items': item_count / count, 'sha256': digest.hexdigest(), 'bytes': path.stat().st_size,
                'format': 'canonical-jsonl; timestamps UTC seconds; prices JSON numbers cents/100',
                'evaluation_eligible': count == 500_000}
    with manifest_path.open('x', encoding='utf-8') as output:
        json.dump(manifest, output, indent=2)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=500_000)
    parser.add_argument('--seed', type=int, default=243751)
    parser.add_argument('--output', default='data/orders-500000.jsonl')
    args = parser.parse_args()
    if args.count < 1:
        parser.error('count debe ser positivo')
    print(json.dumps(write_dataset(args.output, args.count, args.seed), indent=2))
