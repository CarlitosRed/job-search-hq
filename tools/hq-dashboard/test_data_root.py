"""Data root: the app can live in one folder and read its data from another.

Order: JOB_SEARCH_HQ_DATA, then a .job-search-hq-data marker in the folder above the app,
then the app folder itself. All data in this test is fictional.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]
ENV = 'JOB_SEARCH_HQ_DATA'
MARKER = '.job-search-hq-data'
RUNTIME = sorted(p for folder in ('tools/hq-dashboard', 'tools/hq-dashboard/sandbox', 'tools/tracker')
                 for p in (APP / folder).glob('*.py') if not p.name.startswith('test_'))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Clean(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.pop(ENV, None)
        self.addCleanup(self.restore)

    def restore(self):
        os.environ.pop(ENV, None)
        if self.saved is not None:
            os.environ[ENV] = self.saved


class Resolver(Clean):
    def layout(self, folder):
        """tmp/app holds the app files; tmp holds the data."""
        data = Path(folder)
        app = data / 'app'
        (app / 'tools' / 'hq-dashboard').mkdir(parents=True)
        shutil.copyfile(HERE / 'hq_settings.py', app / 'tools' / 'hq-dashboard' / 'hq_settings.py')
        shutil.copyfile(APP / 'settings.example.json', app / 'settings.example.json')
        return data, app, load(app / 'tools' / 'hq-dashboard' / 'hq_settings.py', f'hq_settings_{id(self)}')

    def test_default_is_the_app_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            data, app, settings = self.layout(folder)
            self.assertEqual(Path(settings.app_root()).resolve(), app.resolve())
            self.assertEqual(Path(settings.data_root()).resolve(), app.resolve())

    def test_marker_above_the_app(self):
        with tempfile.TemporaryDirectory() as folder:
            data, app, settings = self.layout(folder)
            (data / MARKER).write_text('', encoding='utf-8')
            self.assertEqual(Path(settings.app_root()).resolve(), app.resolve())
            self.assertEqual(Path(settings.data_root()).resolve(), data.resolve())

    def test_environment_wins(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            data, app, settings = self.layout(folder)
            (data / MARKER).write_text('', encoding='utf-8')
            os.environ[ENV] = other
            self.assertEqual(Path(settings.data_root()).resolve(), Path(other).resolve())

    def test_settings_merge_across_roots(self):
        with tempfile.TemporaryDirectory() as folder:
            data, app, settings = self.layout(folder)
            (data / MARKER).write_text('', encoding='utf-8')
            (data / 'settings.json').write_text(json.dumps({'owner_name': 'Fictional Owner'}), encoding='utf-8')
            values = settings.load()
            self.assertEqual(values['owner_name'], 'Fictional Owner')
            self.assertEqual(values['home_country_label'], 'Home country')  # from the app's example

    def test_settings_in_the_app_folder_are_ignored_when_data_is_elsewhere(self):
        with tempfile.TemporaryDirectory() as folder:
            data, app, settings = self.layout(folder)
            (data / MARKER).write_text('', encoding='utf-8')
            (app / 'settings.json').write_text(json.dumps({'owner_name': 'Wrong Place'}), encoding='utf-8')
            self.assertNotEqual(settings.load()['owner_name'], 'Wrong Place')


class NoClimbing(unittest.TestCase):
    def test_runtime_files_ask_hq_settings_for_their_roots(self):
        climbing = re.compile(r'parents\[[1-9]\]|\.parent\.parent')
        offenders = [f'{p.relative_to(APP)}:{n}' for p in RUNTIME if p.name != 'hq_settings.py'
                     for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1) if climbing.search(line)]
        self.assertEqual(offenders, [])


class AppInsideData(Clean):
    """Full app copy in tmp/app; fictional data in tmp, found through the marker."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='hq-data-root-')
        cls.data = Path(cls.folder.name).resolve()
        cls.app = cls.data / 'app'
        shutil.copytree(APP / 'tools', cls.app / 'tools', ignore=shutil.ignore_patterns('__pycache__', '*.html.bak*'))
        for name in ('settings.example.json', 'rules.example.md'):
            shutil.copyfile(APP / name, cls.app / name)
        (cls.data / MARKER).write_text('', encoding='utf-8')
        (cls.data / 'settings.json').write_text(json.dumps({'owner_name': 'Fictional Data Owner'}), encoding='utf-8')
        (cls.data / 'work').mkdir()
        shutil.copyfile(APP / 'examples' / 'applications-tracker.xlsx', cls.data / 'work' / 'applications-tracker.xlsx')
        (cls.data / 'evidence').mkdir()
        (cls.data / 'evidence' / 'E-900-fictional-data-root.md').write_text(
            '# E-900 fictional\n- level: verified\n- public-safe: yes\n- CV-ready bullets: Fictional data-root claim.\n- limit: none\n',
            encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        for name in [n for n in sys.modules if n.startswith('dr_')]:
            del sys.modules[name]
        cls.folder.cleanup()

    def module(self, rel):
        path = self.app / 'tools' / rel
        sys.path.insert(0, str(path.parent))
        self.addCleanup(lambda: sys.path.remove(str(path.parent)))
        return load(path, 'dr_' + re.sub(r'\W', '_', rel))

    def test_modules_point_root_at_the_data(self):
        for rel in ('hq-dashboard/ai_tasks.py', 'hq-dashboard/pc_files.py', 'hq-dashboard/server.py',
                    'hq-dashboard/profile_builder.py'):
            with self.subTest(rel):
                self.assertEqual(Path(self.module(rel).ROOT).resolve(), self.data)

    def test_tracker_workbook_comes_from_the_data(self):
        os.environ.pop('TRACKER_XLSX', None)
        tracker = self.module('tracker/server.py')
        self.assertEqual(Path(tracker.XLSX).resolve(), self.data / 'work' / 'applications-tracker.xlsx')

    def test_evidence_bank_reads_the_data(self):
        bank = self.module('hq-dashboard/ai_tasks.py').evidence_bank()
        self.assertIn('E-900', json.dumps(bank, default=str))

    def test_documents_default_to_the_data_work_folder(self):
        import inspect
        pc_files = self.module('hq-dashboard/pc_files.py')
        self.assertEqual(Path(inspect.signature(pc_files.documents).parameters['work'].default).resolve(), self.data / 'work')

    def test_pc_page_carries_the_data_owner(self):
        env = {k: v for k, v in os.environ.items() if k != ENV}
        run = subprocess.run([sys.executable, '-I', str(self.app / 'tools' / 'hq-dashboard' / 'port_tracker.py'), '--backend', 'pc'],
                             cwd=self.data, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        page = (self.app / 'tools' / 'hq-dashboard' / 'pc-index.html').read_text(encoding='utf-8')
        self.assertIn('Fictional Data Owner', page)


class SuitesIgnoreTheOwnersDataFolder(unittest.TestCase):
    """A suite that builds its own fictional folder must pass even when JOB_SEARCH_HQ_DATA is set.

    With the setting on, test_profile used to read the other folder's rules and test_lead_ranker
    its settings.
    """
    SUITES = ('test_profile.py', 'test_lead_ranker.py')

    def test_suites_pass_with_the_setting_pointing_at_an_empty_folder(self):
        with tempfile.TemporaryDirectory(prefix='other-data-') as folder:
            env = dict(os.environ, **{ENV: folder})
            for name in self.SUITES:
                r = subprocess.run([sys.executable, '-I', str(HERE / name)], env=env,
                                   capture_output=True, text=True, timeout=600)
                self.assertEqual(r.returncode, 0, name + ' fails when ' + ENV + ' is set: ' + r.stderr[-1500:])
                self.assertEqual(os.listdir(folder), [], name + ' wrote into the data folder from the setting')


if __name__ == '__main__':
    unittest.main(verbosity=2)
