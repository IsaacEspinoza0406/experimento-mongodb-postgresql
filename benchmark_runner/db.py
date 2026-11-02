import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def connect(engine):
    if engine == 'mongodb':
        from pymongo import MongoClient
        return MongoClient('mongodb://mongo:27017', serverSelectionTimeoutMS=120000,
                           socketTimeoutMS=125000, connectTimeoutMS=10000)
    if engine == 'postgresql':
        import pg8000.dbapi
        connection = pg8000.dbapi.connect(host='postgres', user='benchmark', database='orders_bench',
                                         password=os.environ['BENCHMARK_PASSWORD'], timeout=125)
        connection.autocommit = True
        return connection
    raise ValueError(engine)


def prepare(engine, connection, variant='default'):
    if engine == 'mongodb':
        connection.admin.command('ping')
        return
    cur = connection.cursor()
    if variant not in ('default', '4MB', '64MB'):
        raise ValueError('work_mem inválido')
    cur.execute("SET work_mem = '64MB'" if variant == '64MB' else "SET work_mem = '4MB'")
    cur.execute("SET statement_timeout = '120s'")
    cur.execute("SET timezone = 'UTC'")
    cur.close()


def query(engine, connection, query_id, variant='default'):
    if query_id not in ('Q1', 'Q2'):
        raise ValueError(query_id)
    if engine == 'mongodb':
        pipeline = json.loads((ROOT / 'queries/mongodb' / f'{query_id}.json').read_text())
        return list(connection.orders_bench.orders.aggregate(pipeline, allowDiskUse=variant == 'disk', maxTimeMS=120000))
    cur = connection.cursor()
    try:
        cur.execute((ROOT / 'queries/postgresql' / f'{query_id}.sql').read_text())
        return cur.fetchall()
    finally:
        cur.close()
