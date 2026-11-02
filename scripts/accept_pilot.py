import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchestrate import check_full_dataset, source_hash

path = Path(sys.argv[1])
manifest = json.loads((path / 'manifest.json').read_text())
audit = json.loads((path / 'audit.json').read_text())
assert manifest['mode'] == 'pilot' and manifest['status'] == 'complete'
assert manifest['cell']['warmup'] == 60 and manifest['cell']['measure'] == 300 and manifest['cell']['cooldown'] == 60
assert audit['passed'] and audit['attempt_id'] == manifest['attempt_id']
assert manifest['flush']['verified']
assert manifest['source_sha256'] == source_hash(), 'Código cambió desde piloto'
dataset = check_full_dataset()
assert manifest['dataset']['sha256'] == dataset['sha256']
assert manifest['summary']['latency_samples'] > 0 and manifest['summary']['errors_total'] == 0
assert manifest['limits_before']['memory.swap.max'] == '0'
target = Path('config/acceptance.json')
with target.open('x') as output:
    json.dump({'pilot_passed': True, 'pilot': path.as_posix(), 'audit': audit,
               'image_lock_sha256': manifest['image_lock_sha256'], 'source_sha256': source_hash(),
               'dataset_sha256': dataset['sha256']}, output, indent=2)
print('Piloto auditado; matriz puede iniciarse con estado persistente.')
