"""The password state folder must sit outside the app and the data folder (bug B2, 2026-11-13).

The check compared the state folder only with the data folder. When the data folder is
elsewhere, a state folder inside the app checkout passed. All paths here are temporary.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]
SERVER = HERE / 'server.py'
MESSAGE = 'Password state must be outside the repository.'


class StatePath(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix='hq-state-data-'))
        self.outside = Path(tempfile.mkdtemp(prefix='hq-state-outside-'))
        self.addCleanup(shutil.rmtree, self.data, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)

    def run_server(self, state, data_root):
        env = dict(os.environ)
        env['JOB_SEARCH_HQ_DATA'] = str(data_root)
        return subprocess.run([sys.executable, str(SERVER), '--state', str(state), '--unlock'],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_state_inside_app_is_refused_when_data_is_elsewhere(self):
        state = APP / '.test-state-inside-app'
        self.addCleanup(shutil.rmtree, state, ignore_errors=True)
        r = self.run_server(state, self.data)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(MESSAGE, r.stderr)
        self.assertFalse(state.exists(), 'a refused state folder must not be created')

    def test_state_inside_data_folder_is_still_refused(self):
        r = self.run_server(self.data / 'state', self.data)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(MESSAGE, r.stderr)

    def test_state_outside_both_passes_the_check(self):
        r = self.run_server(self.outside / 'state', self.data)
        self.assertNotIn(MESSAGE, r.stderr + r.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
