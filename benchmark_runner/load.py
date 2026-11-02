"""Carga solo en destino vacío; si se interrumpe conserva evidencia y rechaza repetición ciega."""
import argparse
import hashlib
import json
from pathlib import Path
from .db import connect


def insert_batch(cursor, batch):
    # Un statement por lote evita 500k round trips; contenido siempre parametrizado.
    values = ','.join(['(%s,%s::jsonb)'] * len(batch))
    cursor.execute('INSERT INTO orders (id,data) VALUES ' + values, tuple(v for row in batch for v in row))


def pg_documents(connection, cursor):
    connection.autocommit = False
    cursor.execute('DECLARE verify_docs NO SCROLL CURSOR FOR SELECT data FROM orders')
    while True:
        cursor.execute('FETCH FORWARD 1000 FROM verify_docs')
        batch = cursor.fetchall()
        if not batch:
            break
        yield from (r[0] for r in batch)
    cursor.execute('CLOSE verify_docs')
    connection.commit()
    connection.autocommit = True


def load(engine, dataset):
    path = Path(dataset)
    manifest = json.loads(path.with_suffix(path.suffix + '.manifest.json').read_text())
    sha = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
    if sha != manifest['sha256']:
        raise ValueError('Hash dataset diferente')
    connection = connect(engine)
    count = 0
    if engine == 'mongodb':
        collection = connection.orders_bench.orders
        if collection.estimated_document_count():
            raise ValueError('Colección no vacía; verificar intento previo, no sobrescribir')
        batch = []
        with path.open() as source:
            for line in source:
                batch.append(json.loads(line))
                if len(batch) == 1000:
                    collection.insert_many(batch)
                    count += len(batch)
                    if count % 10000 == 0:
                        print(json.dumps({'event': 'load_progress', 'engine': engine, 'inserted': count}), flush=True)
                    batch = []
            if batch:
                collection.insert_many(batch)
                count += len(batch)
        collection.create_index('timestamp')
        collection.create_index('order_id')
        observed = collection.count_documents({})
        indexes = list(collection.list_indexes())
        documents = collection.find({}, {'_id': 0}).batch_size(1000)
    else:
        cur = connection.cursor()
        cur.execute("SELECT to_regclass('public.orders')")
        if cur.fetchone()[0] is not None:
            raise ValueError('Tabla existente; verificar intento previo, no sobrescribir')
        for statement in (Path('queries/postgresql/schema.sql').read_text().split(';')[:2]):
            cur.execute(statement)
        with path.open() as source:
            # Un commit por lote, inserciones parametrizadas; sin cambiar documentos.
            connection.autocommit = False
            batch = []
            for line in source:
                document = json.loads(line)
                batch.append((document['order_id'], line))
                if len(batch) == 1000:
                    insert_batch(cur, batch)
                    connection.commit()
                    count += len(batch)
                    if count % 10000 == 0:
                        print(json.dumps({'event': 'load_progress', 'engine': engine, 'inserted': count}), flush=True)
                    batch = []
            if batch:
                insert_batch(cur, batch)
                connection.commit()
                count += len(batch)
            connection.autocommit = True
        cur.execute('ANALYZE orders')
        cur.execute('SELECT count(*) FROM orders')
        observed = cur.fetchone()[0]
        cur.execute("SELECT indexdef FROM pg_indexes WHERE tablename='orders'")
        indexes = cur.fetchall()
        documents = pg_documents(connection, cur)
    # Hash semántico de TODOS los documentos, no solo conteo/muestreo.
    # Multiconjunto criptográfico: SHA256 por documento, suma modular + XOR + conteo.
    # Evita materializar 500k documentos o imponer un sort adicional durante carga.
    observed_sum = observed_xor = 0
    for document in documents:
        h = int.from_bytes(hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).digest(), 'big')
        observed_sum = (observed_sum + h) % (1 << 256)
        observed_xor ^= h
    expected_sum = expected_xor = 0
    with path.open() as source:
        for line in source:
            document = json.loads(line)
            h = int.from_bytes(hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).digest(), 'big')
            expected_sum = (expected_sum + h) % (1 << 256)
            expected_xor ^= h
    assert observed == count == manifest['orders'], 'Conteo distinto'
    assert (observed_sum, observed_xor) == (expected_sum, expected_xor), 'Documentos distintos'
    connection.close()
    return {'engine': engine, 'dataset_sha256': sha, 'orders': observed, 'indexes': indexes,
            'canonical_multiset_sha256_sum': f'{observed_sum:064x}',
            'canonical_multiset_sha256_xor': f'{observed_xor:064x}', 'verified': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--engine', choices=['mongodb', 'postgresql'], required=True)
    p.add_argument('--dataset', required=True)
    p.add_argument('--evidence', required=True)
    a = p.parse_args()
    result = load(a.engine, a.dataset)
    target = Path(a.evidence)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x') as stream:
        json.dump(result, stream, indent=2, default=str)
    print(json.dumps(result, default=str))
