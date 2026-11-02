import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo


def policy():
    config = json.loads(Path('config/deadline.json').read_text())
    hard = dt.datetime.fromisoformat(config['date'] + 'T' + config['time']).replace(tzinfo=ZoneInfo(config['timezone']))
    return {**config, 'hard_epoch': hard.timestamp(), 'deadline_iso': hard.isoformat(),
            'load_cutoff_epoch': hard.timestamp() - config['shutdown_reserve_seconds']}


def admission(cell, now, observed_overheads=()):
    config = policy()
    # Máximo observado hasta ahora, conservador con pocas muestras. No extrapolar solo un promedio.
    calibration = Path('evidence/deadline/overhead-calibration.json')
    initial = json.loads(calibration.read_text())['observed_overhead_seconds'] if calibration.exists() else 0
    observed = max([initial, *observed_overheads])
    overhead = max(config['minimum_observed_overhead_seconds'], math.ceil(observed) + config['additional_overhead_margin_seconds'])
    required = cell['warmup'] + cell['measure'] + cell['cooldown'] + overhead + config['evidence_reserve_seconds']
    return {'allowed': now + required <= config['load_cutoff_epoch'], 'now_epoch': now,
            'required_seconds': required, 'observed_max_overhead_seconds': observed,
            'overhead_margin_seconds': overhead, **config}
