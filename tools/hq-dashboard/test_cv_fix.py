"""Offline checks for step 05a-4: the Create CV fix.

Job titles and dates reach the AI from a role line in the evidence; the CV prompt asks for claims copied
word for word, newest-first jobs and real section headings; the site, not the AI, writes the name and
contact line. Every entry, posting and setting here is fictional.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import uuid

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ai = load_module('cv_fix_ai', HERE / 'ai_tasks.py')
hq_settings = load_module('cv_fix_settings', HERE / 'hq_settings.py')

CONTACT = 'alex@example.com | +1 555 010 0000 | example.com/alex'


def entry(key, bullet, roles=()):
    lines = [f'# {key} Fictional entry', '- level: stated', '- public-safe: yes', f'- CV-ready bullet: {bullet}']
    lines += [f'- role: {r}' for r in roles]
    lines.append('- limit: no numbers')
    return '\n'.join(lines) + '\n'


class RoleLines(unittest.TestCase):
    """Titles and dates come from `- role:` lines and travel with the entry."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-cvfix-bank-')
        self.root = Path(self.temp.name)
        for name in ('settings.example.json', 'rules.example.md'):
            shutil.copyfile(APP / name, self.root / name)
        ev = self.root / 'evidence'
        ev.mkdir()
        (ev / 'E-001-fictional.md').write_text(entry('E-001', 'Ran the monthly fictional partner process.',
                                                     ['Process Specialist | Example Corp | Jan 2024 to May 2025']), encoding='utf-8')
        (ev / 'E-002-fictional.md').write_text(entry('E-002', 'About 3 years of fictional support work.',
                                                     ['Technical Support, then Data Analyst | Sample Air | Jun 2021 to Jun 2023',
                                                      'Customer Support | Demo Call Center | Jan 2020 to Apr 2021']), encoding='utf-8')
        (ev / 'E-003-fictional.md').write_text(entry('E-003', 'Built a fictional dashboard in a spreadsheet.'), encoding='utf-8')
        (ev / 'E-004-fictional.md').write_text(entry('E-004', 'Wrote fictional requirements documents.',
                                                     ['Business Analyst | Example Corp']), encoding='utf-8')
        (ev / 'E-005-fictional.md').write_text(entry('E-005', 'Led fictional workshops.',
                                                     ['Consultant | Example Corp | sometime last year']), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_role_lines_reach_the_bank(self):
        bank = ai.evidence_bank(self.root)
        self.assertEqual(bank['entries']['E-001']['roles'], ['Process Specialist | Example Corp | Jan 2024 to May 2025'])
        self.assertEqual(bank['entries']['E-002']['roles'],
                         ['Technical Support, then Data Analyst | Sample Air | Jun 2021 to Jun 2023',
                          'Customer Support | Demo Call Center | Jan 2020 to Apr 2021'])

    def test_entry_without_role_lines_keeps_its_shape(self):
        bank = ai.evidence_bank(self.root)
        self.assertIn('E-003', bank['entries'])
        self.assertNotIn('roles', bank['entries']['E-003'])

    def test_bad_role_lines_are_left_out_and_listed(self):
        bank = ai.evidence_bank(self.root)
        self.assertNotIn('E-004', bank['entries'], 'a role line without dates')
        self.assertNotIn('E-005', bank['entries'], 'a role line with unreadable dates')
        listed = {item['id']: item['note'] for item in bank['needs_fixing']}
        self.assertEqual(sorted(listed), ['E-004', 'E-005'])
        for note in listed.values():
            self.assertIn('role', note.lower())

    def test_role_lines_are_in_the_prompt(self):
        prompt = ai.prompt_for('cv', {'company': 'Example', 'text': 'Fictional posting'}, ai.evidence_bank(self.root))
        self.assertIn('Technical Support, then Data Analyst | Sample Air | Jun 2021 to Jun 2023', prompt)

    def test_public_example_entry_has_a_role_line(self):
        bank = ai.evidence_bank(APP)
        self.assertTrue(any(e.get('roles') for e in bank['entries'].values()), 'the fictional example shows the role line')


class CvPrompt(unittest.TestCase):
    """The CV prompt carries the four writing rules and never the contact details."""

    def setUp(self):
        self.bank = {'rubric_version': 'v3.2', 'rules': 'Fictional only.',
                     'entries': {'E-999': {'level': 'stated', 'text': 'Fictional skill.', 'roles': ['Analyst | Example Corp | 2022 to 2024']}}}

    def cv(self):
        return ai.prompt_for('cv', {'company': 'Example', 'text': 'Fictional posting'}, self.bank)

    def test_claims_are_copied_word_for_word(self):
        self.assertIn('word for word', self.cv())

    def test_jobs_newest_first_with_role_headings(self):
        prompt = self.cv()
        self.assertIn('newest first', prompt)
        self.assertIn('### Title | Company | Dates', prompt)

    def test_sections_are_real_headings(self):
        prompt = self.cv()
        self.assertIn('## Experience', prompt)
        self.assertNotIn('Use **bold** only', prompt)

    def test_no_contact_placeholders_and_no_contact_values(self):
        with mock.patch.dict(ai.SETTINGS, {'owner_contact': CONTACT, 'owner_name': 'Alex Example'}):
            for kind in ('cv', 'letter'):
                prompt = ai.prompt_for(kind, {'company': 'Example', 'text': 'Fictional posting'}, self.bank, 'warm')
                self.assertNotIn('[email]', prompt, kind)
                self.assertNotIn('[LinkedIn URL]', prompt, kind)
                self.assertNotIn('alex@example.com', prompt, kind)
                self.assertNotIn('555 010', prompt, kind)


class SiteHead(unittest.TestCase):
    """The saved CV carries the site's name and contact line, never the AI's."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-cvfix-task-')
        self.state = Path(self.temp.name)
        self.bank = {'rubric_version': 'v3.2', 'rules': 'Fictional only.',
                     'entries': {'E-999': {'level': 'stated', 'text': 'Ran the fictional process.',
                                           'roles': ['Analyst | Example Corp | Jan 2024 to May 2025']}}}
        self.cv = None
        self.prompts = []
        from lead_policy_fixtures import fictional_analysis

        def runner(prompt, kind, folder, images):
            self.prompts.append(prompt)
            if kind == 'analyze':
                return {'is_job_posting': True, 'readable': True, 'company': 'Example Company', 'role': 'Example Engineer', 'link': '',
                        'posting_text': '', 'rubric_version': 'v3.2', 'analysis': fictional_analysis(), 'summary': 'Fictional.',
                        'location_ok': 'unclear', 'matches': [], 'gaps': [], 'flags': []}
            return {'cv_markdown': self.cv, 'sources': ['E-999'], 'flags': [],
                    'claims': [{'text': 'Ran the fictional process.', 'evidence': ['E-999']}]}
        self.tasks = ai.Tasks(self.state, runner=runner, bank_loader=lambda: self.bank, link_reader=lambda link: None)

    def tearDown(self):
        self.tasks.stop()
        self.temp.cleanup()

    def run_cv(self, text, name='Alex Example', contact=CONTACT):
        self.cv = text
        with mock.patch.dict(ai.SETTINGS, {'owner_name': name, 'owner_contact': contact}):
            posting = self.tasks.submit({'kind': 'analyze', 'requestId': str(uuid.uuid4()), 'images': [],
                                         'posting': {'company': 'Example Company', 'role': 'Example Engineer', 'link': '',
                                                     'text': 'Fictional posting. ' * 20}})
            self.tasks.process_one()
            self.tasks.submit({'kind': 'cv', 'requestId': str(uuid.uuid4()), 'postingId': posting})
            self.tasks.process_one()
        task = self.tasks.list_tasks()[0]
        self.assertEqual(task['status'], 'done', task['message'])
        return self.tasks.posting(posting)['fit']

    BODY = ('## Summary\nFictional analyst.\n## Experience\n'
            '### Analyst | Example Corp | Jan 2024 to May 2025\n- Ran the fictional process.\n')

    def test_site_writes_name_and_contact_line(self):
        fit = self.run_cv('# Someone Else\n[email] | [phone] | [LinkedIn URL]\n' + self.BODY)
        lines = fit['cv_markdown'].split('\n')
        self.assertEqual(lines[0], '# Alex Example')
        self.assertEqual(lines[1], CONTACT)
        self.assertNotIn('[email]', fit['cv_markdown'])
        self.assertNotIn('Someone Else', fit['cv_markdown'])
        self.assertIn('### Analyst | Example Corp | Jan 2024 to May 2025', fit['cv_markdown'])
        self.assertFalse(any(CONTACT in p or 'alex@example.com' in p for p in self.prompts), 'contact details never reach the AI')

    def test_headline_line_is_kept(self):
        fit = self.run_cv('Business analyst for fictional workflows\n' + self.BODY)
        lines = fit['cv_markdown'].split('\n')
        self.assertEqual(lines[:3], ['# Alex Example', CONTACT, 'Business analyst for fictional workflows'])

    def test_empty_contact_setting_means_no_contact_line(self):
        fit = self.run_cv('# Alex Example\n[email] | [phone] | [LinkedIn URL]\n' + self.BODY, contact='')
        lines = fit['cv_markdown'].split('\n')
        self.assertEqual(lines[0], '# Alex Example')
        self.assertEqual(lines[1], '## Summary')
        self.assertNotIn('[', fit['cv_markdown'])

    def test_body_after_the_head_is_kept_as_written(self):
        body = '## Summary\n\nFictional analyst.\n\n---\n## Experience\n### Analyst | Example Corp | Jan 2024 to May 2025\n- Ran the fictional process.\n'
        fit = self.run_cv('# Alex Example\n[email] | [phone]\n\n' + body)
        self.assertTrue(fit['cv_markdown'].endswith(body), 'only the head changes; blank lines and rules in the body stay')

    def test_newest_first_has_no_order_flag(self):
        fit = self.run_cv(self.BODY + '### Support | Sample Air | Jun 2021 to Jun 2023\n- Fictional support.\n')
        self.assertNotIn('Jobs are not in newest-first order.', fit.get('cv_flags', []))

    def test_wrong_order_is_saved_with_a_flag(self):
        fit = self.run_cv('## Experience\n### Support | Sample Air | Jun 2021 to Jun 2023\n- Fictional support.\n'
                          '### Analyst | Example Corp | Jan 2024 to May 2025\n- Ran the fictional process.\n')
        self.assertIn('Jobs are not in newest-first order.', fit['cv_flags'])

    def test_present_counts_as_newest(self):
        fit = self.run_cv('## Experience\n### Lead | Now Corp | Jun 2025 to Present\n- Fictional.\n'
                          '### Analyst | Example Corp | Jan 2024 to May 2025\n- Ran the fictional process.\n')
        self.assertNotIn('Jobs are not in newest-first order.', fit.get('cv_flags', []))


class ContactSetting(unittest.TestCase):
    def test_example_settings_have_an_empty_contact_line(self):
        example = json.loads((APP / 'settings.example.json').read_text(encoding='utf-8'))
        self.assertEqual(example.get('owner_contact'), '')

    def test_contact_is_not_page_facing(self):
        self.assertNotIn('owner_contact', hq_settings.browser(APP))


if __name__ == '__main__':
    unittest.main(verbosity=2)
