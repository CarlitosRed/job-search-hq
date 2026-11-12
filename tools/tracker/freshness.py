"""Shared job dates, age, scan window and exact duplicate keys."""
from datetime import date, datetime
import importlib.util
from pathlib import Path
import re
import unicodedata

_stage_spec = importlib.util.spec_from_file_location('tracker_freshness_stages', Path(__file__).with_name('stages.py'))
stages = importlib.util.module_from_spec(_stage_spec)
_stage_spec.loader.exec_module(stages)

WINDOW_DAYS = 45
ARCHIVE_DAYS = 60
_rejected = next(s['name'] for s in stages.STAGES if s['id'] == 'rejected')
KEEP_STAGES = tuple(name for name in stages.NAMES
                    if name in stages.DATA['rejected_from'] or name == _rejected)
ARCHIVE_STAGES = tuple(name for name in stages.NAMES if name not in KEEP_STAGES)
SOURCES = ('greenhouse', 'lever', 'ashby', 'linkedin', 'page', 'first_seen')


def sheet_date(name):
    if isinstance(name, datetime):
        return name.date().isoformat()
    if isinstance(name, date):
        return name.isoformat()
    match = re.search(r'\d{4}-\d{2}-\d{2}', str(name or ''))
    if match:
        try:
            return date.fromisoformat(match.group()).isoformat()
        except ValueError:
            pass
    return None


def parse_posted(value):
    posted = sheet_date(value)
    if posted is None:
        return None, 'first_seen'
    text = str(value or '')
    for source, pattern in (('greenhouse', r'\b(?:greenhouse|gh)\b'),
                            ('lever', r'\blever\b'), ('ashby', r'\bashby\b'),
                            ('linkedin', r'\blinkedin\b')):
        if re.search(pattern, text, re.I):
            return posted, source
    return posted, 'page'


def is_about(source, text):
    return source in ('linkedin', 'ashby', 'first_seen') or bool(
        re.match(r'about\b', str(text or '').strip(), re.I))


def age_days(posted_on, first_seen, today=None):
    dates = [date.fromisoformat(d) for d in (sheet_date(posted_on), sheet_date(first_seen)) if d]
    return ((today or date.today()) - min(dates)).days if dates else None


def job_key(company, role, place):
    def clean(value):
        text = unicodedata.normalize('NFKC', str(value or '')).casefold()
        return ' '.join(''.join(c if c.isalpha() or c.isdigit() else ' ' for c in text).split())
    key = tuple(clean(value) for value in (company, role, place))
    return key if key[0] and key[1] else None


def in_window(age):
    return age is None or age <= WINDOW_DAYS


def recheck(stage, age):
    return stage in stages.DATA['rejected_from'] or in_window(age)
