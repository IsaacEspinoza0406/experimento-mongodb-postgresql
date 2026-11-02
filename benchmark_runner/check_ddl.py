import json
from pathlib import Path
from .db import connect

connection = connect('postgresql')
cur = connection.cursor()
result = {'attempted': "CREATE INDEX verify_timestamp_cast ON orders (((data->>'timestamp')::timestamptz))"}
try:
    cur.execute(result['attempted'])
    result['accepted'] = True
    cur.execute('DROP INDEX verify_timestamp_cast')
except Exception as error:
    result.update(accepted=False, error=str(error))
finally:
    cur.close()
    connection.close()
with Path('evidence/smoke/postgres-cast-ddl.json').open('x') as output:
    json.dump(result, output, indent=2)
print(json.dumps(result))
