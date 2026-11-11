"""Offline checks for interview rounds (build step 01b).

Every workbook here is fictional and lives in a temporary folder.
"""
from datetime import date
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

import openpyxl

HERE = Path(__file__).resolve().parent
COLUMN = 'Interview rounds'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load_module('tracker_stage_fixtures_for_rounds', HERE / 'test_stages.py')
store = fixtures.store
ROWS = fixtures.ROWS


class Rounds(unittest.TestCase):
    """Rounds live in the database: Round 1 on entering Interview, Add round, edit, the Excel copy."""

    sqlite = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hq-rounds-')
        self.xlsx, _ = fixtures.make_tracker(self.tmp.name)
        wb = openpyxl.load_workbook(self.xlsx)
        for row in wb['Jobs'].iter_rows(min_row=2):
            row[7].value = next(e for c, _, _, _, e in ROWS if c == row[1].value)
        wb['New (2026-01-06)']['H2'] = 'Review'
        wb.save(self.xlsx)
        wb.close()
        if self.sqlite:
            self.assertTrue(store.migrate(self.xlsx, apply=True)['ok'])
        self.t = fixtures.tracker_for(self.xlsx)
        self.today = date.today().isoformat()

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def row_of(self, company):
        return 2 + [r[0] for r in ROWS].index(company)

    def ref(self, company, **extra):
        return {'sheet': 'Jobs', 'row': self.row_of(company),
                'expect': {'Company': company, 'Role': 'Example Role'}, **extra}

    def move(self, company, status):
        self.t.update(self.ref(company, status=status))

    def job(self, company):
        return next(j for j in self.t.load()['jobs'] if j['Company'] == company)

    def rounds(self, company):
        return [(r['round'], r['date'], r['type'], r['note']) for r in self.job(company)['_rounds']]

    def add(self, company, **fields):
        return self.t.rounds(self.ref(company, action='add', **fields))

    def edit(self, company, round_id, **fields):
        return self.t.rounds(self.ref(company, action='edit', id=round_id, **fields))

    def db(self, sql, args=()):
        db = sqlite3.connect(store.db_path(self.xlsx))
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()

    def revision(self):
        return int(self.db("SELECT value FROM meta WHERE key='revision'")[0][0])

    def mirror(self):
        wb = openpyxl.load_workbook(self.xlsx)
        try:
            return {ws.title: [[c.value for c in r] for r in ws.iter_rows()] for ws in wb}
        finally:
            wb.close()

    # Round 1 on entering Interview
    def test_entering_interview_adds_round_one(self):
        self.assertEqual(self.rounds('Sent Co'), [])
        self.move('Sent Co', 'Interview')
        self.assertEqual(self.rounds('Sent Co'), [(1, self.today, '', '')])

    def test_round_one_added_only_once(self):
        self.move('Sent Co', 'Interview')
        self.move('Sent Co', 'Applied')   # undo
        self.move('Sent Co', 'Interview')
        self.move('Sent Co', 'Offer')
        self.assertEqual(self.rounds('Sent Co'), [(1, self.today, '', '')])

    def test_other_moves_add_no_round(self):
        self.move('Fit Co', 'Apply')
        self.move('Sent Co', 'Rejected')
        self.assertEqual(self.db('SELECT COUNT(*) FROM interview_rounds')[0][0], 0)

    def test_job_already_in_interview_gets_no_round_from_other_edits(self):
        self.t.update(self.ref('Talk Co', note='A note'))
        self.assertEqual(self.rounds('Talk Co'), [])

    def test_new_row_added_in_interview_gets_round_one(self):
        self.t.add({'sheet': 'Jobs', 'Company': 'Direct Co', 'Role': 'Direct Role', 'My status': 'Interview'})
        self.assertEqual(self.rounds('Direct Co'), [(1, self.today, '', '')])

    # Add round
    def test_add_round_numbers_in_order(self):
        self.add('Talk Co')
        self.add('Talk Co', date='2026-02-10', type='Recruiter call', note='Went well')
        self.add('Talk Co', type='Panel')
        self.assertEqual(self.rounds('Talk Co'), [(1, self.today, '', ''),
                                                 (2, '2026-02-10', 'Recruiter call', 'Went well'),
                                                 (3, self.today, 'Panel', '')])

    def test_add_round_only_in_interview(self):
        for company in ('Fit Co', 'Sent Co', 'Win Co', 'No Co'):
            with self.assertRaises(ValueError):
                self.add(company)
        self.assertEqual(self.db('SELECT COUNT(*) FROM interview_rounds')[0][0], 0)

    def test_add_round_checks_the_row(self):
        with self.assertRaises(self.t.Conflict):
            self.t.rounds({'sheet': 'Jobs', 'row': self.row_of('Talk Co'), 'action': 'add',
                           'expect': {'Company': 'Win Co', 'Role': 'Example Role'}})
        with self.assertRaises(ValueError):
            self.t.rounds({'sheet': 'Contacts', 'row': 2, 'action': 'add', 'expect': {'Person': 'Example Person'}})
        with self.assertRaises(ValueError):
            self.t.rounds(self.ref('Talk Co', action='delete'))

    def test_bad_fields_refused(self):
        for fields in ({'date': '10/02/2026'}, {'date': '2026-02-30'}, {'type': '=HYPERLINK("x")'},
                       {'note': ' =cmd'}, {'type': 'x' * 61}, {'note': 'x' * 281}, {'note': 5}):
            with self.assertRaises(ValueError, msg=fields):
                self.add('Talk Co', **fields)
        self.assertEqual(self.rounds('Talk Co'), [])

    # Edit
    def test_edit_round(self):
        self.add('Talk Co', type='Recruiter call')
        round_id = self.job('Talk Co')['_rounds'][0]['id']
        self.edit('Talk Co', round_id, date='2026-03-01', type='Hiring manager', note='Next: case study')
        self.assertEqual(self.rounds('Talk Co'), [(1, '2026-03-01', 'Hiring manager', 'Next: case study')])
        self.edit('Talk Co', round_id, note='')
        self.assertEqual(self.rounds('Talk Co'), [(1, '2026-03-01', 'Hiring manager', '')])

    def test_edit_after_interview_still_allowed(self):
        self.add('Talk Co', type='Final')
        round_id = self.job('Talk Co')['_rounds'][0]['id']
        self.move('Talk Co', 'Offer')
        self.edit('Talk Co', round_id, note='Offer came after this one')
        self.assertEqual(self.rounds('Talk Co')[0][3], 'Offer came after this one')

    def test_edit_refuses_another_jobs_round(self):
        self.add('Talk Co')
        round_id = self.job('Talk Co')['_rounds'][0]['id']
        self.move('Sent Co', 'Interview')
        with self.assertRaises(ValueError):
            self.edit('Sent Co', round_id, note='Wrong job')
        with self.assertRaises(ValueError):
            self.edit('Talk Co', 9999, note='No such round')
        self.assertEqual(self.rounds('Talk Co'), [(1, self.today, '', '')])

    # Bookkeeping
    def test_round_writes_bump_revision_and_keep_moves(self):
        before = self.revision()
        self.add('Talk Co')
        round_id = self.job('Talk Co')['_rounds'][0]['id']
        self.edit('Talk Co', round_id, type='Phone screen')
        self.assertEqual(self.revision(), before + 2)
        self.assertEqual(self.db('SELECT COUNT(*) FROM moves')[0][0], 0)
        self.assertEqual(self.t.load()['version'], 'db-' + str(before + 2))

    def test_rounds_listed_for_every_job(self):
        jobs = self.t.load()['jobs']
        self.assertTrue(all(j['_rounds'] == [] for j in jobs))

    def test_old_jobs_and_contacts_untouched(self):
        before = self.db('SELECT fields FROM old_jobs'), self.db('SELECT fields FROM contacts')
        self.add('Talk Co', type='Recruiter call')
        self.assertEqual((self.db('SELECT fields FROM old_jobs'), self.db('SELECT fields FROM contacts')), before)

    # The Excel copy
    def test_mirror_has_one_readable_column(self):
        self.add('Talk Co', date='2026-02-10', type='Recruiter call', note='Went well')
        self.add('Talk Co', date='2026-02-17', type='Panel')
        sheets = self.mirror()
        jobs = sheets['Jobs']
        self.assertEqual(jobs[0].count(COLUMN), 1)
        self.assertEqual(jobs[0][-1], COLUMN)
        talk = next(r for r in jobs[1:] if r[1] == 'Talk Co')
        self.assertEqual(talk[-1], 'Round 1 · 2026-02-10 · Recruiter call · Went well\nRound 2 · 2026-02-17 · Panel')
        fit = next(r for r in jobs[1:] if r[1] == 'Fit Co')
        self.assertIn(fit[-1], (None, ''))
        self.assertEqual(sheets['New (2026-01-06)'][0][-1], COLUMN)
        self.assertNotIn(COLUMN, sheets['Contacts'][0])

    def test_mirror_column_never_doubles_or_enters_the_data(self):
        self.add('Talk Co', type='Recruiter call')
        self.t.update(self.ref('Fit Co', note='Edited after a round'))
        self.move('Fit Co', 'Apply')
        self.assertEqual(self.mirror()['Jobs'][0].count(COLUMN), 1)
        for (fields,) in self.db('SELECT fields FROM jobs'):
            self.assertNotIn('Round 1 ·', fields)
        layout = self.db("SELECT header FROM layouts WHERE source='main' AND sheet='Jobs'")[0][0]
        self.assertNotIn(COLUMN, layout)
        self.assertNotIn(COLUMN, [str(c) for s in self.t.load()['sheets'] for c in s['columns']])


class RoundsExcelOnly(Rounds):
    """Without the database the board still works; rounds are refused with a plain message."""

    sqlite = False

    def test_excel_mode_refuses_rounds_plainly(self):
        with self.assertRaises(ValueError) as caught:
            self.add('Talk Co')
        self.assertIn('database', str(caught.exception))
        self.move('Sent Co', 'Interview')
        self.assertEqual(self.job('Sent Co')['My status'], 'Interview')
        self.assertEqual(self.rounds('Sent Co'), [])
        self.assertNotIn(COLUMN, self.mirror()['Jobs'][0])


for _name in [n for n in dir(Rounds) if n.startswith('test_')]:
    setattr(RoundsExcelOnly, _name, None)
RoundsExcelOnly.test_excel_mode_refuses_rounds_plainly = RoundsExcelOnly.__dict__['test_excel_mode_refuses_rounds_plainly']
RoundsExcelOnly.test_rounds_listed_for_every_job = Rounds.test_rounds_listed_for_every_job


if __name__ == '__main__':
    unittest.main(verbosity=2)
