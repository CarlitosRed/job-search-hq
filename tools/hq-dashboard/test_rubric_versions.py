"""Static activation interlock checks; no real workbook/model/network.

"""
from pathlib import Path
import re
import sys
import unittest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import lead_policy as policy


class VersionCopies(unittest.TestCase):
    def test_all_active_version_declarations_agree(self):
        paths=[HERE/'lead_ranker.py',HERE/'lead_policy.py',HERE/'ai_tasks.py',HERE/'profile_builder.py',
               HERE/'hosted/tools.js',HERE/'hosted/lead-policy.js']
        for path in paths:
            text=path.read_text(encoding='utf-8').split('## Retained previous version')[0]
            versions=re.findall(r"(?:RUBRIC_VERSION\s*=\s*['\"]|RUBRIC_VERSION:\s*)(v[\d.]+)",text)
            self.assertTrue(versions,path)
            self.assertEqual(set(versions),{policy.RUBRIC_VERSION},path)

    def test_no_old_weights_in_active_tools(self):
        old=r'30\s*%|25\s*%|30/25/15/15/15|archetype\s+0\.30|4\.0\+ APPLY|3\.0 to 3\.9 CONSIDER|caps the score at 2\.5'
        for folder in (ROOT/'tools',):
            for path in folder.rglob('*'):
                if path.suffix not in ('.py','.js','.md') or path.name.startswith('test_') or path==Path(__file__):continue
                text=path.read_text(encoding='utf-8')
                if text.startswith('<!-- HISTORICAL SCORING SNAPSHOT:'):continue
                text=text.split('## Retained previous version')[0]
                self.assertIsNone(re.search(old,text,re.I),path)


if __name__=='__main__':unittest.main()
