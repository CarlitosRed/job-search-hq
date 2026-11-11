"""The shared board stages and transition rules."""
import json
from pathlib import Path

DATA = json.loads(Path(__file__).with_name('stages.json').read_text(encoding='utf-8'))
STAGES = DATA['stages']
NAMES = tuple(s['name'] for s in STAGES)
NEW_ROW = DATA['new_row']
_REJECTED = next(s['name'] for s in STAGES if s['id'] == 'rejected')


def allowed(before, after):
    return after in NAMES and (after != _REJECTED or before == _REJECTED
                               or before in DATA['rejected_from'])


def from_legacy(status, has_fit):
    status = str(status or '').strip()
    if status in NAMES:
        return status
    if not status:
        status = next(iter(DATA['legacy']))
    target = DATA['legacy'].get(status)
    return DATA['without_fit'].get(target, target) if not has_fit else target
