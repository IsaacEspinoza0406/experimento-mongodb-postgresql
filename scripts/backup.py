"""Respaldo local sin secretos. Copiar ZIP/hash a almacenamiento independiente elegido."""
import datetime as dt
import hashlib
import json
import zipfile
from pathlib import Path

stamp = dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S%fZ')
target = Path('backups') / f'evidence-{stamp}.zip'
target.parent.mkdir(exist_ok=True)
files = sorted({p for root in ['results', 'evidence', 'config', 'queries', 'docs'] for p in Path(root).rglob('*') if p.is_file() and p.suffix != '.partial'})
inventory = []
with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
        inventory.append({'path': path.as_posix(), 'bytes': path.stat().st_size, 'sha256': digest})
        archive.write(path, path.as_posix())
    archive.writestr('inventory.json', json.dumps(inventory, indent=2))
digest = hashlib.file_digest(target.open('rb'), 'sha256').hexdigest()
target.with_suffix('.sha256').write_text(digest + '  ' + target.name + '\n')
print(target, digest, len(files), 'archivos; respaldo local, copiar a destino independiente')
