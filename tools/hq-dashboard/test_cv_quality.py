"""Offline checks for step 05a: CV quality and document records.

Every file, posting and evidence entry here is fictional and lives in a temporary folder.
The pull-request line below is the exact line that reached a real CV, with the account and
repository names swapped for fictional ones.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
import uuid

import openpyxl

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


files = load_module('cv_quality_files', HERE / 'pc_files.py')
conv = files.converter()
ai = load_module('cv_quality_ai', HERE / 'ai_tasks.py')
store = load_module('cv_quality_store', HERE.parent / 'tracker' / 'store.py')
PREFIX = files.SETTINGS['document_prefix']

PR_LINE = ("PR #77 (github.com/example-user/review-tool/pull/77): Codex reviewed Claude's review-relay changes; "
           "the tool could silently corrupt BOM-less UTF-16 input and verify the wrong comment when posts overlap, "
           "although all 12 new tests passed.")
TERMS = ['BOM', 'UTF-16', 'UTF-8']
CLEAN_CV = ('# Alex Example\n[email] | [phone] | [LinkedIn URL]\n## Experience\n'
            '- Reviewed 21 merged PRs for a fictional team\n'
            '- Published the tool at https://github.com/example-user/review-tool\n'
            '- Moved a fictional tracker from a spreadsheet to a database: 179 rows in, 179 rows out')


def workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Fictional jobs'
    ws.append(['#', 'Company', 'Role', 'My status', 'Date applied', 'Notes', 'Link'])
    ws.append([1, 'Example Company', 'Example Engineer', 'Review', '', 'Example only', 'https://example.com/job'])
    ws.append([2, 'Second Company', 'Data Analyst', 'Apply', '', '', 'https://example.com/job2'])
    wb.save(path)
    wb.close()
    return path


def docx_file(path, text='Fictional document body.'):
    import docx
    d = docx.Document()
    d.add_paragraph(text)
    d.save(path)
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Readability(unittest.TestCase):
    """The check blocks and explains; it never rewords."""

    def test_pull_request_line_is_blocked_with_plain_notes(self):
        notes = conv.readability_problems(PR_LINE, TERMS)
        self.assertTrue(notes)
        joined = ' '.join(notes)
        for fragment in ('#77', 'pull/77', 'BOM', 'UTF-16'):
            self.assertIn(fragment, joined)
        for note in notes:
            self.assertIsInstance(note, str)
            self.assertNotRegex(note, r'\bE-\d{3}\b', 'notes are plain English')

    def test_each_rule(self):
        blocked = {
            'Shipped the fix in PR #77.': '#77',
            'Closed #115 after the review.': '#115',
            'See github.com/example-user/review-tool/pull/77 for details.': 'pull/77',
            'Reported in https://github.com/example-user/review-tool/issues/12': '/issues/',
            'Code at https://github.com/example-user/review-tool/blob/main/x.py': '/blob/',
            'Code at https://github.com/example-user/review-tool/tree/main/tools': '/tree/',
            'Fixed in https://github.com/example-user/review-tool/commit/abc123': '/commit/',
            'Built the parser (see E-050).': 'E-050',
            'Follows rule 12 of the office.': 'rule 12',
            'Logged as leak 3 in the register.': 'leak 3',
            'Wrote BOM-less files.': 'BOM',
            'Read UTF-8 input.': 'UTF-8',
        }
        for line, fragment in blocked.items():
            with self.subTest(line=line):
                notes = conv.readability_problems(line, TERMS)
                self.assertTrue(notes, line)
                self.assertIn(fragment, ' '.join(notes))
        for line in ('Reviewed 21 merged PRs for a fictional team.',
                     'Published the tool at https://github.com/example-user/review-tool',
                     'Published the tool at https://github.com/example-user/review-tool/',
                     'Moved 179 rows from a spreadsheet to a database, 179 rows out.',
                     'Built a bombproof checklist for a fictional team.'):
            with self.subTest(line=line):
                self.assertEqual(conv.readability_problems(line, TERMS), [])

    def test_clean_cv_passes_and_text_is_untouched(self):
        self.assertEqual(conv.readability_problems(CLEAN_CV, TERMS), [])
        self.assertIsNone(conv.draft_problem(CLEAN_CV, 'cv'))

    def test_public_default_terms(self):
        example = json.loads((APP / 'settings.example.json').read_text(encoding='utf-8'))
        self.assertEqual(example['cv_blocked_terms'], TERMS)
        self.assertIn('cv_blocked_terms', files.SETTINGS)


class BankLint(unittest.TestCase):
    """Failing CV-ready lines never reach the AI and are listed for fixing."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-cvq-bank-')
        self.root = Path(self.temp.name)
        for name in ('settings.example.json', 'rules.example.md'):
            shutil.copyfile(APP / name, self.root / name)
        (self.root / 'evidence').mkdir()
        entries = {
            'E-001': 'Reviewed 21 merged PRs for a fictional team.',
            'E-002': 'none. Letter-ready wording: "I have not used the fictional tool."',
            'E-003': 'Spanish: native. English: B2 (see E-001).',
            'E-004': PR_LINE,
            'E-005': 'not cv-ready',
            'E-006': 'Moved a fictional tracker to a database: 179 rows in, 179 rows out.',
        }
        for key, bullet in entries.items():
            (self.root / 'evidence' / f'{key}-fictional.md').write_text(
                f'# {key} Fictional entry\n- level: stated\n- public-safe: yes\n- CV-ready bullet: {bullet}\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_bad_lines_left_out_and_listed(self):
        bank = ai.evidence_bank(self.root)
        self.assertEqual(sorted(bank['entries']), ['E-001', 'E-006'])
        listed = {item['id']: item['note'] for item in bank['needs_fixing']}
        self.assertEqual(sorted(listed), ['E-002', 'E-003', 'E-004'], 'not cv-ready stays silent; the rest are listed')
        for note in listed.values():
            self.assertIsInstance(note, str)
            self.assertTrue(note.strip())

    def test_evidence_notes_for_the_documents_tab(self):
        notes = ai.evidence_notes(self.root)
        self.assertEqual(sorted(n['id'] for n in notes), ['E-002', 'E-003', 'E-004'])
        for entry in (self.root / 'evidence').glob('E-*.md'):
            entry.unlink()
        self.assertEqual(ai.evidence_notes(self.root), [], 'an empty bank lists nothing and never raises')


class Records(unittest.TestCase):
    """The documents table, the schema upgrade and the backfill."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-cvq-store-')
        self.dir = Path(self.temp.name)
        self.book = workbook(self.dir / 'tracker.xlsx')
        self.assertTrue(store.migrate(self.book, apply=True)['ok'])
        self.db = store.db_path(self.book)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            rows = db.execute('SELECT * FROM jobs ORDER BY id').fetchall()
            revision = db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return rows, revision, tables

    def test_upgrade_adds_documents_table_and_keeps_rows(self):
        with closing(sqlite3.connect(self.db)) as db, db:  # a database made before 05a has no documents table
            db.execute('DROP TABLE IF EXISTS documents')
        rows, revision, tables = self.snapshot()
        self.assertNotIn('documents', tables)
        s = store.Store(self.db)
        s.close()
        after_rows, after_revision, after_tables = self.snapshot()
        self.assertIn('documents', after_tables)
        self.assertEqual((after_rows, after_revision), (rows, revision))
        with closing(sqlite3.connect(self.db)) as db, db:
            columns = [r[1] for r in db.execute('PRAGMA table_info(documents)')]
        self.assertEqual(columns, ['id', 'job_id', 'kind', 'file_name', 'sha256', 'made_by', 'created',
                                   'posting_id', 'evidence_ids', 'checks'])

    def test_add_and_list_documents(self):
        s = store.Store(self.db)
        try:
            key = s.add_document(kind='cv', file_name=PREFIX + 'CV_Example_Company_Example_Engineer.docx', sha256='a' * 64,
                                 made_by='site', created='2026-01-05T10:00:00+00:00', job_id='Fictional jobs|2',
                                 posting_id=str(uuid.uuid4()), evidence_ids=['E-001'], checks=['markers', 'readability'])
            s.db.commit()
            for bad in ({'kind': 'memo'}, {'made_by': 'robot'}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    s.add_document(**{'kind': 'cv', 'file_name': 'x.docx', 'sha256': 'b' * 64, 'made_by': 'site',
                                      'created': '2026-01-05', **bad})
            listed = s.documents()
        finally:
            s.close()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['id'], key)
        self.assertEqual(listed[0]['evidence_ids'], ['E-001'])
        self.assertEqual(listed[0]['checks'], ['markers', 'readability'])
        self.assertEqual(listed[0]['made_by'], 'site')

    def test_backfill_registers_each_file_once_and_never_touches_them(self):
        work = self.dir / 'work'
        work.mkdir()
        made = [docx_file(work / (PREFIX + 'CV_Example_Company_Example_Engineer.docx')),
                docx_file(work / (PREFIX + 'CL_Example_Company_Example_Engineer.docx')),
                docx_file(work / (PREFIX + 'CV_Second_Company_Data_Analyst.docx'))]
        docx_file(work / 'notes.docx')
        (work / 'report-fictional.md').write_text('# Fictional', encoding='utf-8')
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in work.iterdir()}
        dry = store.backfill_documents(self.book, work, prefix=PREFIX, apply=False)
        self.assertEqual((dry['ok'], dry['found'], dry['added']), (True, 3, 0))
        s = store.Store(self.db)
        self.assertEqual(s.documents(), [], 'a dry run writes nothing')
        s.close()
        done = store.backfill_documents(self.book, work, prefix=PREFIX, apply=True)
        self.assertEqual((done['ok'], done['found'], done['added']), (True, 3, 3))
        again = store.backfill_documents(self.book, work, prefix=PREFIX, apply=True)
        self.assertEqual(again['added'], 0, 'running it twice adds nothing')
        s = store.Store(self.db)
        rows = s.documents()
        s.close()
        self.assertEqual(sorted(r['file_name'] for r in rows), sorted(p.name for p in made))
        for r in rows:
            self.assertEqual(r['made_by'], 'outside')
            self.assertIsNone(r['job_id'])
            self.assertEqual(r['sha256'], sha(work / r['file_name']))
            self.assertEqual(r['kind'], 'letter' if '_CL_' in '_' + r['file_name'].removeprefix(PREFIX) else 'cv')
            self.assertTrue(r['created'][:4].isdigit(), 'the file date is kept')
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in work.iterdir()}, 'files byte-identical')

    def test_backfill_command_dry_run_by_default(self):
        work = self.dir / 'work'
        work.mkdir()
        docx_file(work / (PREFIX + 'CV_Example_Company_Example_Engineer.docx'))
        script = HERE.parent / 'tracker' / 'backfill_documents.py'
        env = {k: v for k, v in os.environ.items() if k != 'JOB_SEARCH_HQ_DATA'}
        run = subprocess.run([sys.executable, '-I', str(script), '--workbook', str(self.book), '--work', str(work), '--prefix', PREFIX],
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('1', run.stdout)
        s = store.Store(self.db)
        self.assertEqual(s.documents(), [])
        s.close()
        run = subprocess.run([sys.executable, '-I', str(script), '--workbook', str(self.book), '--work', str(work), '--prefix', PREFIX, '--apply'],
                             capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        s = store.Store(self.db)
        self.assertEqual(len(s.documents()), 1)
        s.close()


class Export(unittest.TestCase):
    """Site exports land in work/ with rule-12 names, are recorded, and never overwrite."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-cvq-export-')
        self.dir = Path(self.temp.name)
        self.state = self.dir / 'state'
        self.state.mkdir()
        self.work = self.dir / 'work'
        self.work.mkdir()
        self.book = workbook(self.dir / 'tracker.xlsx')
        self.assertTrue(store.migrate(self.book, apply=True)['ok'])
        self.db = store.db_path(self.book)
        self.posting = str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def record(self, cv=CLEAN_CV, letter=None):
        fit = {'cv_markdown': cv, 'sources': ['E-001', 'E-006']}
        if letter:
            fit['cover_letter_markdown'] = letter
        return {'company': 'Example Company', 'role': 'Example Engineer', 'jobId': 'Fictional jobs|2', 'fit': fit}

    def export(self, kind='cv', record=None):
        return files.export_draft(self.state, self.posting, kind, record or self.record(), work=self.work,
                                  store_path=self.db, blocked_terms=TERMS)

    def rows(self):
        s = store.Store(self.db)
        try:
            return s.documents()
        finally:
            s.close()

    def test_cv_lands_in_work_with_rule_12_name_and_record(self):
        path = self.export()
        self.assertEqual(path.parent.resolve(), self.work.resolve())
        self.assertEqual(path.name, PREFIX + 'CV_Example_Company_Example_Engineer.docx')
        self.assertFalse((self.state / 'generated-documents').exists(), 'nothing goes to the state folder')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['kind'], row['file_name'], row['made_by'], row['job_id'], row['posting_id']),
                         ('cv', path.name, 'site', 'Fictional jobs|2', self.posting))
        self.assertEqual(row['sha256'], sha(path))
        self.assertEqual(row['evidence_ids'], ['E-001', 'E-006'])
        self.assertIn('readability', row['checks'])
        self.assertIn('markers', row['checks'])

    def test_letter_uses_cl_name(self):
        letter = 'Dear Example Company team,\n\nI moved a fictional tracker to a database.\n\nKind regards,\nAlex Example'
        path = self.export('letter', self.record(letter=letter))
        self.assertEqual(path.name, PREFIX + 'CL_Example_Company_Example_Engineer.docx')
        self.assertEqual(self.rows()[0]['kind'], 'letter')

    def test_existing_file_is_never_overwritten(self):
        outside = docx_file(self.work / (PREFIX + 'CV_Example_Company_Example_Engineer.docx'), 'Made outside the site.')
        before = outside.read_bytes()
        path = self.export()
        self.assertEqual(path.name, PREFIX + 'CV_Example_Company_Example_Engineer_2.docx')
        self.assertEqual(outside.read_bytes(), before)
        other = self.record(cv=CLEAN_CV + '\n- Wrote a second fictional plan')
        third = files.export_draft(self.state, str(uuid.uuid4()), 'cv', other, work=self.work, store_path=self.db, blocked_terms=TERMS)
        self.assertEqual(third.name, PREFIX + 'CV_Example_Company_Example_Engineer_3.docx')
        self.assertEqual(outside.read_bytes(), before)

    def test_same_draft_twice_is_one_file(self):
        first = self.export()
        second = self.export()
        self.assertEqual(first, second)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(list(self.work.glob('*.docx'))), 1)

    def test_unreadable_draft_is_blocked_and_nothing_is_written(self):
        with self.assertRaises(ValueError) as caught:
            self.export(record=self.record(cv=CLEAN_CV + '\n- ' + PR_LINE))
        self.assertIn('#77', str(caught.exception))
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.rows(), [])

    def test_documents_listing_reads_the_record(self):
        outside = docx_file(self.work / (PREFIX + 'CV_Example_Company_Example_Engineer.docx'))
        unknown = docx_file(self.work / (PREFIX + 'CV_Second_Company_Data_Analyst.docx'))
        store.backfill_documents(self.book, self.work, prefix=PREFIX, apply=True)
        unknown_later = docx_file(self.work / (PREFIX + 'CL_Third_Company_Writer.docx'))
        site = self.export()
        listed = {d['name']: d for d in files.documents(self.work, store_path=self.db)}
        self.assertEqual(listed[site.name]['made_by'], 'site')
        self.assertEqual(listed[site.name]['job_id'], 'Fictional jobs|2')
        self.assertTrue(listed[site.name]['created'])
        for path in (outside, unknown, unknown_later):
            self.assertEqual(listed[path.name]['made_by'], 'outside', 'a file with no site record is never the site\'s')
            self.assertIsNone(listed[path.name]['job_id'])
        without_store = {d['name']: d['made_by'] for d in files.documents(self.work)}
        self.assertEqual(set(without_store.values()), {'outside'}, 'without a database nothing claims to be made by the site')


class DraftTask(unittest.TestCase):
    """The AI task refuses a draft that reads like internal notes, before it is saved."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-cvq-task-')
        self.state = Path(self.temp.name)
        self.bank = {'rubric_version': 'v3.2', 'rules': 'Fictional only.', 'entries': {'E-999': {'level': 'stated', 'text': 'Fictional skill.'}}}
        self.cv = None
        from lead_policy_fixtures import fictional_analysis

        def runner(prompt, kind, folder, images):
            if kind == 'analyze':
                return {'is_job_posting': True, 'readable': True, 'company': 'Example Company', 'role': 'Example Engineer', 'link': '',
                        'posting_text': '', 'rubric_version': 'v3.2', 'analysis': fictional_analysis(), 'summary': 'Fictional.',
                        'location_ok': 'unclear', 'matches': [], 'gaps': [], 'flags': []}
            return {'cv_markdown': self.cv, 'sources': ['E-999'], 'flags': [],
                    'claims': [{'text': 'Fictional skill.', 'evidence': ['E-999']}]}
        self.tasks = ai.Tasks(self.state, runner=runner, bank_loader=lambda: self.bank, link_reader=lambda link: None)

    def tearDown(self):
        self.tasks.stop()
        self.temp.cleanup()

    def run_cv(self, text):
        p = {'kind': 'analyze', 'requestId': str(uuid.uuid4()),
             'posting': {'company': 'Example Company', 'role': 'Example Engineer', 'link': '', 'text': 'Fictional posting. ' * 20}, 'images': []}
        posting = self.tasks.submit(p)
        self.tasks.process_one()
        self.cv = text
        self.tasks.submit({'kind': 'cv', 'requestId': str(uuid.uuid4()), 'postingId': posting})
        self.tasks.process_one()
        return posting, self.tasks.list_tasks()[0]

    def test_pull_request_draft_fails_with_plain_note(self):
        posting, task = self.run_cv('# Alex Example\n## Experience\n- Fictional skill.\n- ' + PR_LINE)
        self.assertEqual(task['status'], 'failed')
        self.assertIn('#77', task['message'])
        self.assertNotIn('cv_markdown', self.tasks.posting(posting)['fit'])

    def test_clean_draft_is_saved(self):
        posting, task = self.run_cv('# Alex Example\n## Experience\n- Fictional skill.\n- Reviewed 21 merged PRs.')
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('21 merged PRs', self.tasks.posting(posting)['fit']['cv_markdown'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
