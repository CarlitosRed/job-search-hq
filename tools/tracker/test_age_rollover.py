"""Job ages follow the calendar, not the data version (bug B1, 2026-11-13).

The tracker caches the parsed jobs per data version. A day can pass with no edit,
so the cache must not freeze each job's age. All data here is fictional.
"""
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import openpyxl

HERE = Path(__file__).resolve().parent
HEAD = ['#', 'Company', 'Role', 'Where', 'Posted', 'My status', 'Link', 'Notes']
_count = 0


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracker_for(xlsx):
    global _count
    _count += 1
    module = load_module(f'tracker_rollover_{_count}', HERE / 'server.py')
    module.XLSX = Path(xlsx).resolve()
    module.BACKUPS = module.XLSX.parent / '.tracker-backups'
    return module


def fake_date(shift_days):
    """A date class whose today() is the real today plus shift_days."""
    real = date

    class Shifted(real):
        @classmethod
        def today(cls):
            d = real.today() + timedelta(days=shift_days)
            return cls(d.year, d.month, d.day)
    return Shifted


class AgeRollover(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='hq-rollover-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.xlsx = self.tmp / 'applications-tracker.xlsx'
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Jobs'
        ws.append(HEAD)
        posted = date.today() - timedelta(days=5)
        ws.append([1, 'Example Co', 'Example Analyst', 'Remote', posted, 'Review', 'https://example.com/jobs/1', None])
        ws.append([2, 'Sample Labs', 'Sample Engineer', 'Remote', posted - timedelta(days=10), 'Tracked',
                   'https://example.com/jobs/2', None])
        wb.save(self.xlsx)

    def migrate(self):
        load_module('rollover_store', HERE / 'store.py').migrate(self.xlsx, old=None, apply=True)

    def shift(self, t, days):
        """Move the clock for the tracker and its date rules; restore after the test."""
        fake = fake_date(days)
        for mod in (t, t.store.freshness):
            old = mod.date
            mod.date = fake
            self.addCleanup(setattr, mod, 'date', old)

    def ages(self, t):
        data = json.loads(t.cached())
        return data['today'], {j['Company']: j['_age'] for j in data['jobs']}

    def check_rollover(self, t):
        today1, ages1 = self.ages(t)
        self.assertEqual(ages1['Example Co'], 5)
        self.assertEqual(ages1['Sample Labs'], 15)
        version = t.version()
        self.shift(t, 1)
        self.assertEqual(t.version(), version, 'no edit happened; the data version must not move')
        today2, ages2 = self.ages(t)
        self.assertEqual(today2, (date.fromisoformat(today1) + timedelta(days=1)).isoformat())
        self.assertEqual(ages2['Example Co'], 6, 'age must follow the calendar even when the data is unchanged')
        self.assertEqual(ages2['Sample Labs'], 16)

    def test_excel_mode_age_rolls_over(self):
        self.check_rollover(tracker_for(self.xlsx))

    def test_sqlite_mode_age_rolls_over(self):
        self.migrate()
        t = tracker_for(self.xlsx)
        self.assertEqual(json.loads(t.cached())['store'], 'sqlite')
        self.check_rollover(t)

    def test_same_day_reuses_the_cache(self):
        """The slow workbook parse still happens once per data version within a day."""
        t = tracker_for(self.xlsx)
        calls = []
        real_load = t.load
        t.load = lambda *a, **k: calls.append(1) or real_load(*a, **k)
        t.cached()
        t.cached()
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
