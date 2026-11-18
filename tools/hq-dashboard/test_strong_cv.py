"""Offline Strong CV checks. Every person, employer and posting is fictional."""
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
spec = importlib.util.spec_from_file_location('strong_cv_ai', HERE / 'ai_tasks.py')
ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai)

ROLE = 'Analyst | Example Corp | Jan 2024 to May 2025'
BANK = {'rubric_version': ai.RUBRIC_VERSION, 'rules': 'Fictional only.', 'entries': {
    'E-001': {'text': 'Built 3 reports with SQL.', 'roles': [ROLE], 'skills': ['Reporting', 'SQL', 'Python']},
    'E-002': {'text': 'Checked fictional records.', 'roles': [ROLE], 'skills': ['sql', 'Record checks']},
    'E-003': {'text': 'Directed fictional agents.', 'skills': ['Agent design']},
}}
MATCHES = [
    {'requirement': 'SQL reporting', 'importance': 'must', 'level': 'strong', 'evidence': 'E-001'},
    {'requirement': 'Record checks', 'importance': 'nice', 'level': 'partial', 'evidence': 'E-002'},
    {'requirement': 'Automation', 'importance': 'must', 'level': 'partial', 'evidence': 'E-001, E-003'},
    {'requirement': 'Missing skill', 'importance': 'must', 'level': 'gap', 'evidence': 'E-002'},
    {'requirement': 'Reports', 'importance': 'nice', 'level': 'strong', 'evidence': 'E-001'},
    {'requirement': 'Data', 'importance': 'nice', 'level': 'partial', 'evidence': 'E-001'},
]
CV = ('Fictional analyst\n## Summary\nBuilt 3 reports with SQL.\n## Experience\n### ' + ROLE +
      '\n- Checked fictional records.\n- Built 3 reports with SQL.\n## Projects\n- Directed fictional agents.\n'
      '## Skills\n- Invented skill\n## Education\nFictional training.\n')
CLAIMS = [{'text': e['text'], 'evidence': [key]} for key, e in BANK['entries'].items()]
SUMMARY_CLAIMS = [{'text': text, 'kind': 'summary', 'evidence': ['E-001']}
                  for text in ('Fictional analyst', 'Built 3 reports with SQL.')]
HEADING_PROBLEM = 'The CV used a heading HQ does not accept (use ## for sections and ### for jobs). Try again.'


class SkillsBank(unittest.TestCase):
    def test_skills_validation_and_old_entry(self):
        with tempfile.TemporaryDirectory(prefix='hq-strong-bank-') as temp:
            root = Path(temp)
            for name in ('settings.example.json', 'rules.example.md'):
                shutil.copyfile(APP / name, root / name)
            (root / 'evidence').mkdir()
            lines = [None, 'SQL, Reporting, C++', '', 'SQL,', 'A' * 41, 'Writes reports.', 'SQL; Python']
            for index, skills in enumerate(lines, 1):
                text = '# Fictional\n- level: stated\n- public-safe: yes\n- CV-ready bullet: Checked fictional records.\n'
                if skills is not None:
                    text += '- skills: ' + skills + '\n'
                (root / 'evidence' / f'E-{index:03}-fictional.md').write_text(text, encoding='utf-8')
            bank = ai.evidence_bank(root)
            self.assertNotIn('skills', bank['entries']['E-001'])
            self.assertEqual(bank['entries']['E-002']['skills'], ['SQL', 'Reporting', 'C++'])
            self.assertEqual([e['id'] for e in bank['needs_fixing']], [f'E-{i:03}' for i in range(3, 8)])
            self.assertTrue(all('skills' in e['note'].lower() for e in bank['needs_fixing']))


class StrongTasks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hq-strong-task-')
        self.prompts = []
        self.output = {'cv_markdown': CV, 'sources': list(BANK['entries']), 'claims': CLAIMS + SUMMARY_CLAIMS, 'flags': []}
        def runner(prompt, kind, folder, images):
            self.prompts.append(prompt)
            return self.output
        self.tasks = ai.Tasks(Path(self.temp.name), runner=runner, bank_loader=lambda: BANK,
                              link_reader=lambda link: None)
        self.settings = mock.patch.dict(ai.SETTINGS, {'owner_name': 'Alex Example', 'owner_contact': 'alex@example.com',
            'cv_skill_groups': {'Data': ['Python', 'SQL', 'Reporting'], 'Empty': ['Unused']},
            'cv_ai_role_words': ['AI', 'agent', 'agentic', 'LLM', 'automation', 'machine learning', 'prompt']})
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.tasks.stop()
        self.temp.cleanup()

    def draft(self, matches=MATCHES, title='Fictional analyst', text='SQL reporting'):
        record = {'company': 'Example Corp', 'role': title, 'text': text, 'fit': {}}
        if matches is not None:
            record['fit']['matches'] = matches
        posting_id = str(uuid.uuid4())
        with self.tasks.connect() as db, db:
            db.execute('INSERT INTO postings VALUES(?,?)', (posting_id, json.dumps(record)))
        key = self.tasks.submit({'kind': 'cv', 'requestId': str(uuid.uuid4()), 'postingId': posting_id})
        self.tasks.process_one()
        task = next(t for t in self.tasks.list_tasks() if t['id'] == key)
        return task, self.tasks.posting(posting_id)['fit']

    def test_skills_are_built_from_cited_entries(self):
        self.output['sources'] = ['E-002', 'E-001']
        self.output['claims'] = CLAIMS[:2] + SUMMARY_CLAIMS
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        cv = fit['cv_markdown']
        self.assertIn('- **Data:** Reporting, SQL, Python', cv)
        self.assertIn('- **Other:** Record checks', cv)
        for missing in ('Invented skill', 'Agent design', '**Empty:**', ', sql'):
            self.assertNotIn(missing, cv)
        self.assertEqual(cv.count('## Skills'), 1)
        self.assertLess(cv.index('## Skills'), cv.index('## Education'))
        self.assertLess(cv.index('**Data:**'), cv.index('**Other:**'))

    def test_no_skills_means_no_section_and_no_education_means_append(self):
        with mock.patch.dict(BANK['entries'], {'E-001': {'text': BANK['entries']['E-001']['text']}}):
            self.output.update(sources=['E-001'], claims=CLAIMS[:1] + SUMMARY_CLAIMS)
            task, fit = self.draft()
            self.assertEqual(task['status'], 'done', task['message'])
            self.assertNotIn('## Skills', fit['cv_markdown'])
        self.output['cv_markdown'] = CV.split('## Education')[0]
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertTrue(fit['cv_markdown'].rstrip().endswith('- **Data:** Reporting, SQL, Python'))

    def test_posting_match_is_whole_word(self):
        task, fit = self.draft(text='PostgreSQL Python')
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('- **Data:** Python, Reporting, SQL', fit['cv_markdown'])

    def summary(self, text):
        self.output['cv_markdown'] = '## Summary\n' + text
        self.output['sources'] = ['E-001']
        self.output['claims'] = [{'text': sentence, 'kind': 'summary', 'evidence': ['E-001']}
                                 for sentence in ai.re.split(r'(?<=[.!?])\s+', text)]
        return self.draft()[0]

    def test_unmapped_headline_and_summary_fail(self):
        for headline, summary, missing in [
                ('Experienced at Sample Air\n', 'Built 300 reports.', 'Experienced at Sample Air'),
                ('', 'Built 300 reports.', 'Built 300 reports.'),
                ('', 'Built 3 reports with SQL. Built 300 reports.', 'Built 300 reports.')]:
            with self.subTest(missing=missing):
                self.output.update(cv_markdown=headline + '## Summary\n' + summary +
                                   '\n## Experience\n- Checked fictional records.',
                                   sources=['E-001', 'E-002'], claims=[CLAIMS[1]])
                if summary.startswith('Built 3 '):
                    self.output['claims'].append(SUMMARY_CLAIMS[1])
                task, _ = self.draft()
                self.assertEqual(task['status'], 'failed')
                self.assertIn(missing, task['message'])

    def test_bullet_claim_covers_summary_only_with_exact_evidence(self):
        for kind in (None, 'bullet'):
            for heading in ('', '## Summary\n'):
                for text, status in [('Built 3 reports with SQL.', 'done'),
                                     ('Built 3 excellent reports with SQL.', 'failed')]:
                    with self.subTest(kind=kind, heading=heading, text=text):
                        claim = {'text': text, 'evidence': ['E-001']}
                        if kind:
                            claim['kind'] = kind
                        self.output.update(cv_markdown=heading + text,
                                           sources=['E-001'], claims=[claim])
                        task, fit = self.draft()
                        self.assertEqual(task['status'], status, task['message'])
                        if status == 'done':
                            self.assertIn(text, fit['cv_markdown'])
                        else:
                            self.assertIn('word for word', task['message'])

    def test_bullet_claim_can_cover_multiple_summary_sentences(self):
        text = 'Built 3 reports with SQL. Checked fictional records.'
        with mock.patch.dict(BANK['entries'], {'E-001': {'text': text}}):
            for kind in (None, 'bullet'):
                with self.subTest(kind=kind):
                    claim = {'text': text, 'evidence': ['E-001']}
                    if kind:
                        claim['kind'] = kind
                    self.output.update(cv_markdown='## Summary\n' + text,
                                       sources=['E-001'], claims=[claim])
                    task, fit = self.draft()
                    self.assertEqual(task['status'], 'done', task['message'])
                    self.assertIn(text, fit['cv_markdown'])

    def test_summary_prose_after_bullet_block_is_still_covered(self):
        self.output.update(cv_markdown='## Summary\n- Checked fictional records.\n\nBuilt 300 reports.',
                           sources=['E-002'], claims=[CLAIMS[1]])
        task, _ = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('Built 300 reports.', task['message'])

    def test_summary_prose_after_bullet_without_blank_line_needs_coverage(self):
        self.output.update(cv_markdown='## Summary\n- Checked fictional records.\nBuilt 300 reports.',
                           sources=['E-002'], claims=[CLAIMS[1]])
        task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('Built 300 reports.', task['message'])
        self.assertNotIn('cv_markdown', fit)

    def test_summary_bullet_and_continuation_need_complete_ordinary_claim(self):
        block = 'Checked fictional records.\n  Built 3 reports.'
        with mock.patch.dict(BANK['entries'], {'E-002': {'text': block}}):
            for text, status in [('Checked fictional records.', 'failed'), (block, 'done')]:
                with self.subTest(text=text):
                    self.output.update(cv_markdown='## Summary\n- ' + block,
                                       sources=['E-002'], claims=[{'text': text, 'evidence': ['E-002']}])
                    task, fit = self.draft()
                    self.assertEqual(task['status'], status, task['message'])
                    if status == 'failed':
                        self.assertIn('Built 3 reports.', task['message'])
                        self.assertNotIn('cv_markdown', fit)

    def test_unmapped_summary_bullet_fails(self):
        self.output.update(cv_markdown='## Summary\n- Built 300 reports.\n'
                           '## Experience\n- Checked fictional records.',
                           sources=['E-002'], claims=[CLAIMS[1]])
        task, _ = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('Built 300 reports.', task['message'])

    def test_summary_body_after_bullet_can_have_its_own_summary_claim(self):
        self.output.update(cv_markdown='## Summary\n- Checked fictional records.\nBuilt 3 reports with SQL.',
                           sources=['E-001', 'E-002'], claims=[CLAIMS[1], SUMMARY_CLAIMS[1]])
        task, _ = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])

    def test_single_hash_summary_is_rejected_before_summary_validation(self):
        self.output.update(cv_markdown='# Fictional Name\n# Summary\n- Checked fictional records.\n'
                           'Built 300 reports.', sources=['E-002'], claims=[CLAIMS[1]])
        task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['message'], HEADING_PROBLEM)
        self.assertNotIn('cv_markdown', fit)

    def test_supported_summary_and_headline_pass(self):
        self.assertEqual(self.summary('Built 3 reports with SQL.')['status'], 'done')
        self.output['cv_markdown'] = 'Analyst with SQL\n## Summary\nBuilt 3 reports with SQL.'
        self.output['claims'].append({'text': 'Analyst with SQL', 'kind': 'summary', 'evidence': ['E-001']})
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertTrue(fit['cv_markdown'].startswith('# Alex Example\nalex@example.com\nAnalyst with SQL'))

    def test_unsupported_summary_number_capital_and_skill_fail(self):
        for text, token in [('Built 30 reports.', '30'), ('Worked at Sample Air.', 'Sample'),
                            ('Experienced in agent design.', 'Agent design')]:
            with self.subTest(text=text):
                task = self.summary(text)
                self.assertEqual(task['status'], 'failed')
                self.assertIn(token.lower(), task['message'].lower())
                self.assertIn('cited evidence', task['message'])

    def test_each_summary_sentence_checks_its_capital_words(self):
        self.assertEqual(self.summary('Built reports. Delivered reports.')['status'], 'done')
        self.assertEqual(self.summary('Built reports. Worked at Sample Air.')['status'], 'failed')
        self.assertEqual(self.summary('3 Sample reports.')['status'], 'failed')

    def test_summary_numbers_need_the_entire_number(self):
        with mock.patch.dict(BANK['entries'], {'E-001': {'text': 'Built 3.5 reports.'}}):
            self.assertEqual(self.summary('Built 3 reports.')['status'], 'failed')
            self.assertEqual(self.summary('Built 3.5 reports.')['status'], 'done')

    def test_bullets_need_exact_evidence_even_without_kind(self):
        for kind in (None, 'bullet'):
            self.output.update(cv_markdown='## Experience\n- Built 3 excellent reports.', sources=['E-001'],
                               claims=[{'text': 'Built 3 excellent reports.', 'evidence': ['E-001']}])
            if kind:
                self.output['claims'][0]['kind'] = kind
            task, _ = self.draft()
            self.assertEqual(task['status'], 'failed')
            self.assertIn('word for word', task['message'])

    def test_summary_kind_on_exact_role_heading_saves_draft(self):
        self.output['claims'].append({'text': ROLE, 'kind': 'summary', 'evidence': ['E-001']})
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('### Analyst | Example Corp | Jan 2024 to May 2025', fit['cv_markdown'])

    def test_summary_kind_on_exact_evidence_is_ordinary_wherever_it_sits(self):
        for heading in ('', '## Summary\n', '## Experience\n', '## Projects\n'):
            with self.subTest(heading=heading):
                self.output.update(cv_markdown=heading + '- Checked fictional records.',
                                   sources=['E-002'], claims=[{'text': 'Checked fictional records.',
                                   'kind': 'summary', 'evidence': ['E-002']}])
                task, fit = self.draft()
                self.assertEqual(task['status'], 'done', task['message'])
                self.assertIn('- Checked fictional records.', fit['cv_markdown'])

    def test_summary_kind_cannot_bypass_bullet_evidence(self):
        for section in ('Experience', 'Projects', 'Summary', None):
            with self.subTest(section=section):
                heading='## ' + section + '\n' if section else ''
                self.output.update(cv_markdown=heading + '- Led global operations.',
                                   sources=['E-002'], claims=[{'text': 'Led global operations.',
                                   'kind': 'summary', 'evidence': ['E-002']}])
                task, fit = self.draft()
                self.assertEqual(task['status'], 'failed')
                self.assertIn('headline or Summary', task['message'])
                self.assertNotIn('cv_markdown', fit)

    def test_summary_kind_cannot_describe_non_summary_body(self):
        self.output.update(cv_markdown='## Experience\nLed global operations.', sources=['E-002'],
                           claims=[{'text': 'Led global operations.', 'kind': 'summary', 'evidence': ['E-002']}])
        task, _ = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('headline or Summary', task['message'])

    def test_summary_text_repeated_in_bullet_needs_exact_bullet_claim(self):
        self.output.update(cv_markdown='## Summary\nLed global operations.\n'
                           '## Experience\n- Led global operations.', sources=['E-002'],
                           claims=[{'text': 'Led global operations.', 'kind': 'summary', 'evidence': ['E-002']}])
        task, _ = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('word-for-word', task['message'])

    def test_relevance_prompt_and_stable_sort_without_drops(self):
        record = {'fit': {'matches': MATCHES}}
        ranked = ai.cv_evidence(record, BANK)
        self.assertEqual(list(ranked), ['E-001', 'E-003', 'E-002'])
        self.assertEqual([e['score'] for e in ranked.values()], [9, 2, 1])
        self.assertEqual(ranked['E-001']['requirements'], ['SQL reporting', 'Automation', 'Reports'])
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        payload = json.loads(self.prompts[-1].split('EVIDENCE BANK JSON\n')[1].split('\nUNTRUSTED POSTING JSON')[0])
        self.assertEqual(list(payload), list(ranked))
        self.assertEqual(payload['E-001']['score'], 9)
        self.assertLess(fit['cv_markdown'].index('- Built 3'), fit['cv_markdown'].index('- Checked'))
        for claim in CLAIMS:
            self.assertIn('- ' + claim['text'], fit['cv_markdown'])
        self.assertIn('two most recent roles', self.prompts[-1])
        self.assertIn('2-3 sentences', self.prompts[-1])

    def test_ai_role_title_or_must_moves_projects(self):
        for title, matches in [('AI analyst', []), ('Fictional analyst', MATCHES)]:
            task, fit = self.draft(matches=matches, title=title)
            self.assertEqual(task['status'], 'done', task['message'])
            self.assertLess(fit['cv_markdown'].index('## Projects'), fit['cv_markdown'].index('## Experience'))

    def test_non_ai_nice_and_substrings_leave_order(self):
        matches = [{'importance': 'nice', 'requirement': 'AI'}]
        task, fit = self.draft(matches=matches, title='Retail analyst', text='AI automation')
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertLess(fit['cv_markdown'].index('## Experience'), fit['cv_markdown'].index('## Projects'))

    def test_old_record_has_zero_scores_and_keeps_bullet_order(self):
        self.assertTrue(all(e['score'] == 0 for e in ai.cv_evidence({}, BANK).values()))
        task, fit = self.draft(matches=None)
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertLess(fit['cv_markdown'].index('- Checked'), fit['cv_markdown'].index('- Built 3'))

    def test_sort_is_stable_and_stays_inside_each_role(self):
        first = 'Checked fictional records.'
        second = 'Built 3 reports with SQL.'
        text = ('## Experience\n### ' + ROLE + '\n- ' + first + '\n- ' + second +
                '\n### Support | Sample Air | Jan 2021 to Dec 2022\n- ' + first + '\n- ' + second)
        self.output['cv_markdown'] = text
        self.output['claims'] = CLAIMS[:2]
        task, fit = self.draft(matches=[])
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertEqual(fit['cv_markdown'].count('- ' + first), 2)
        self.assertEqual(fit['cv_markdown'].count('- ' + second), 2)
        for role in fit['cv_markdown'].split('### ')[1:]:
            self.assertLess(role.index('- ' + first), role.index('- ' + second))
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        for role in fit['cv_markdown'].split('### ')[1:]:
            self.assertLess(role.index('- ' + second), role.index('- ' + first))

    def test_nul_before_bold_role_is_rejected_before_heading_check_or_transform(self):
        support = 'Support | Sample Air | Jan 2021 to Dec 2022'
        with mock.patch.dict(BANK['entries'], {
                'E-001': dict(BANK['entries']['E-001'], roles=[support])}):
            self.output.update(cv_markdown='## Experience\n### ' + ROLE +
                               '\n- Checked fictional records.\n\u0000**' + support +
                               '**\n- Built 3 reports with SQL.',
                               sources=['E-001', 'E-002'], claims=CLAIMS[:2] + [
                                   {'text': ROLE, 'evidence': ['E-002']},
                                   {'text': support, 'evidence': ['E-001']}])
            with mock.patch.object(ai, 'cv_heading_problem', wraps=ai.cv_heading_problem) as headings, \
                    mock.patch.object(ai, 'strong_cv', wraps=ai.strong_cv) as transform:
                task, fit = self.draft(matches=MATCHES[:2])
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['message'], 'The CV contains hidden characters. Try again.')
        self.assertNotIn('cv_markdown', fit)
        headings.assert_not_called()
        transform.assert_not_called()

    def test_normal_cv_passes_export_cleanup_guard(self):
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertEqual(ai._cleaner.plain(CV), CV)
        self.assertEqual(ai._cleaner.plain(fit['cv_markdown']), fit['cv_markdown'])
        for claim in CLAIMS:
            self.assertIn(claim['text'], fit['cv_markdown'])

    def test_bold_role_is_rejected_before_sort_can_change_employer(self):
        support = 'Support | Sample Air | Jan 2021 to Dec 2022'
        with mock.patch.dict(BANK['entries'], {
                'E-001': dict(BANK['entries']['E-001'], roles=[support])}):
            self.output.update(cv_markdown='## Experience\n### ' + ROLE +
                               '\n- Checked fictional records.\n**' + support +
                               '**\n- Built 3 reports with SQL.',
                               sources=['E-001', 'E-002'], claims=CLAIMS[:2] + [
                                   {'text': ROLE, 'evidence': ['E-002']},
                                   {'text': support, 'evidence': ['E-001']}])
            with mock.patch.object(ai, 'strong_cv', wraps=ai.strong_cv) as transform:
                task, fit = self.draft(matches=MATCHES[:2])
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['message'], HEADING_PROBLEM)
        self.assertNotIn('cv_markdown', fit)
        transform.assert_not_called()

    def test_single_hash_projects_without_name_or_summary_is_rejected_before_move(self):
        self.output.update(cv_markdown='## Experience\n- Checked fictional records.\n'
                           '# Projects\n- Directed fictional agents.',
                           sources=['E-002', 'E-003'], claims=CLAIMS[1:])
        with mock.patch.object(ai, 'strong_cv', wraps=ai.strong_cv) as transform:
            task, fit = self.draft(title='AI analyst')
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['message'], HEADING_PROBLEM)
        self.assertNotIn('cv_markdown', fit)
        transform.assert_not_called()

    def test_canonical_projects_without_name_or_summary_keeps_section_after_move(self):
        self.output.update(cv_markdown='## Experience\n- Checked fictional records.\n'
                           '## Projects\n- Directed fictional agents.',
                           sources=['E-002', 'E-003'], claims=CLAIMS[1:])
        task, fit = self.draft(title='AI analyst')
        self.assertEqual(task['status'], 'done', task['message'])
        head, sections = ai.cv_layout.parse(fit['cv_markdown'])
        self.assertEqual(head, ['# Alex Example', 'alex@example.com'])
        self.assertEqual([s['name'] for s in sections], ['Projects', 'Experience', 'Skills'])
        self.assertEqual(sections[0]['items'], ['- Directed fictional agents.'])
        self.assertEqual(sections[1]['items'], ['- Checked fictional records.'])

    def test_sort_moves_complete_multiline_bullet_blocks(self):
        multiline = 'Checked records.\n  Reduced errors.'
        for continuation in ('  Reduced errors.', 'Reduced errors.'):
            with self.subTest(continuation=continuation):
                multiline = 'Checked records.\n' + continuation
                with mock.patch.dict(BANK['entries'], {
                        'E-002': dict(BANK['entries']['E-002'], text=multiline),
                        'E-001': dict(BANK['entries']['E-001'], text='Built reports.')}):
                    self.output.update(cv_markdown='## Experience\n### ' + ROLE + '\n- ' +
                                       multiline + '\n- Built reports.', sources=['E-001', 'E-002'],
                                       claims=[{'text': multiline, 'evidence': ['E-002']},
                                               {'text': 'Built reports.', 'evidence': ['E-001']}])
                    task, fit = self.draft()
                    self.assertEqual(task['status'], 'done', task['message'])
                    self.assertIn('- Built reports.\n- ' + multiline, fit['cv_markdown'])
                    for claim in self.output['claims']:
                        self.assertIn(claim['text'], fit['cv_markdown'])

    def test_final_evidence_map_checked_after_transforms(self):
        # A valid input claim disappears when the AI Skills section is removed.
        self.output.update(cv_markdown='## Skills\n- Checked fictional records.',
                           sources=['E-002'], claims=[CLAIMS[1]])
        task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('evidence map', task['message'])
        self.assertNotIn('cv_markdown', fit)

    def test_final_evidence_map_checked_after_header_replacement(self):
        with mock.patch.dict(BANK['entries'], {'E-002': {'text': '# Fictional Name'}}):
            self.output.update(cv_markdown='# Fictional Name\n## Experience', sources=['E-002'],
                               claims=[{'text': '# Fictional Name', 'evidence': ['E-002']}])
            task, _ = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('evidence map', task['message'])

    def test_skill_settings_are_case_insensitive(self):
        with mock.patch.dict(ai.SETTINGS, {'cv_skill_groups': {'data': ['sql', 'REPORTING', 'python']}}):
            task, fit = self.draft(text='sql REPORTING')
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('- **data:** Reporting, SQL, Python', fit['cv_markdown'])

    def test_heading_whitespace_case_and_colon_share_normalization(self):
        for space in ('  ', '\t'):
            with self.subTest(space=space):
                self.output['cv_markdown'] = CV.replace('## Skills', '##' + space + 'sKiLlS:  ').replace(
                    '## Education', '##' + space + 'eDuCaTiOn : ').replace(
                    '## Projects', '##' + space + 'pRoJeCtS: ').replace(
                    '## Experience', '##' + space + 'eXpErIeNcE: ')
                task, fit = self.draft(title='AI analyst')
                self.assertEqual(task['status'], 'done', task['message'])
                cv = fit['cv_markdown']
                self.assertNotIn('Invented skill', cv)
                self.assertEqual(cv.count('## Skills'), 1)
                names = [ai.cv_layout.section_heading('## ' + s['name'])
                         for s in ai.cv_layout.parse(cv)[1]]
                self.assertEqual(names, ['summary', 'projects', 'experience', 'skills', 'education'])

    def test_extra_space_skills_with_no_bank_skills_is_removed(self):
        with mock.patch.dict(BANK['entries'], {'E-002': {'text': 'Checked fictional records.'}}):
            self.output.update(cv_markdown='## Experience\n- Checked fictional records.\n'
                               '##  Skills\n- Neurosurgery', sources=['E-002'], claims=[CLAIMS[1]])
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertNotIn('Neurosurgery', fit['cv_markdown'])
        self.assertNotIn('Skills', fit['cv_markdown'])

    def test_single_hash_skills_after_section_is_rejected(self):
        with mock.patch.dict(BANK['entries'], {'E-002': {'text': 'Checked fictional records.'}}):
            self.output.update(cv_markdown='## Experience\n- Checked fictional records.\n'
                               '# Skills\n- Neurosurgery', sources=['E-002'], claims=[CLAIMS[1]])
            task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['message'], HEADING_PROBLEM)
        self.assertNotIn('cv_markdown', fit)

    def test_single_hash_sections_are_rejected_before_transforms(self):
        self.output['cv_markdown'] = CV.replace('## Projects', '# Projects').replace(
            '## Skills', '# Skills').replace('## Education', '# Education')
        task, fit = self.draft(title='AI analyst')
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['message'], HEADING_PROBLEM)
        self.assertNotIn('cv_markdown', fit)

    def test_single_hash_section_is_rejected_before_sort(self):
        self.output.update(cv_markdown='## Experience\n### ' + ROLE +
                           '\n- Checked fictional records.\n# Projects\n- Built 3 reports with SQL.',
                           sources=['E-001', 'E-002'], claims=CLAIMS[:2])
        task, fit = self.draft(title='Retail analyst', matches=MATCHES[:2])
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['message'], HEADING_PROBLEM)
        self.assertNotIn('cv_markdown', fit)

    def test_final_skills_guard_rejects_duplicate_or_changed_sections(self):
        transform = ai.strong_cv
        for extra in ('\n# Skills\n- Neurosurgery', '\n- Neurosurgery'):
            with self.subTest(extra=extra), mock.patch.object(
                    ai, 'strong_cv', side_effect=lambda *args: transform(*args) + extra):
                self.output.update(cv_markdown='## Experience\n- Checked fictional records.',
                                   sources=['E-002'], claims=[CLAIMS[1]])
                task, fit = self.draft()
                self.assertEqual(task['status'], 'failed')
                self.assertIn('Skills', task['message'])
                self.assertNotIn('cv_markdown', fit)

    def test_final_skills_guard_runs_after_site_header(self):
        with mock.patch.dict(ai.SETTINGS, {'owner_contact': '# Skills\n- Neurosurgery'}):
            self.output.update(cv_markdown='## Experience\n- Checked fictional records.',
                               sources=['E-002'], claims=[CLAIMS[1]])
            task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('Skills', task['message'])
        self.assertNotIn('cv_markdown', fit)

    def test_final_skills_guard_rejects_section_when_no_skills_are_cited(self):
        transform = ai.strong_cv
        with mock.patch.dict(BANK['entries'], {'E-002': {'text': 'Checked fictional records.'}}), \
                mock.patch.object(ai, 'strong_cv', side_effect=lambda *args:
                                  transform(*args) + '\n# Skills\n- Neurosurgery'):
            self.output.update(cv_markdown='## Experience\n- Checked fictional records.',
                               sources=['E-002'], claims=[CLAIMS[1]])
            task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('Skills', task['message'])
        self.assertNotIn('cv_markdown', fit)

    def test_phone_fake_cv_requires_exact_evidence_punctuation(self):
        self.output.update(cv_markdown='## Fictional CV\nFictional skill.', sources=['E-999'],
                           claims=[{'text': 'Fictional skill.', 'evidence': ['E-999']}])
        with mock.patch.dict(BANK['entries'], {'E-999': {'text': 'Fictional skill'}}):
            task, _ = self.draft(matches=None)
        self.assertEqual(task['status'], 'failed')
        self.assertIn('word for word', task['message'])
        with mock.patch.dict(BANK['entries'], {'E-999': {'text': 'Fictional skill.'}}):
            task, fit = self.draft(matches=None)
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('Fictional CV', fit['cv_markdown'])

    def test_generated_skills_still_obey_hard_word_limit(self):
        text = '## Experience\n- Built 3 reports with SQL.\n' + 'word ' * 584
        self.output.update(cv_markdown=text, sources=['E-001'], claims=CLAIMS[:1])
        self.assertLessEqual(ai.word_count(text), 600)
        with mock.patch.dict(BANK['entries'], {'E-001': dict(BANK['entries']['E-001'], skills=['Skill ' + str(i) for i in range(10)])}):
            task, _ = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('too long', task['message'])


class PublicContract(unittest.TestCase):
    def test_canonical_heading_check_uses_rendered_sections_and_roles(self):
        for newline in ('\n', '\r\n'):
            for heading in ('**Support | Sample Air | Jan 2021 to Dec 2022**',
                            '**Support | Sample Air** | Jan 2021 to Dec 2022',
                            '  **Fictional project (Oct 2026)**  ', '**Fictional role**'):
                with self.subTest(newline=newline, heading=heading):
                    content = newline.join(['# Fictional Name', 'Fictional headline',
                                            '## Experience', heading, '- Checked fictional records.'])
                    self.assertEqual(ai.cv_heading_problem(content), HEADING_PROBLEM)

    def test_canonical_heading_check_preserves_head_and_body_formatting(self):
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=newline):
                content = newline.join(['# Fictional Name', 'alex@example.com', '**Fictional headline**',
                                        '##  Experience:', '### ' + ROLE,
                                        '- **Checked fictional records.**',
                                        'Ordinary **bold** prose.', '##\tProjects',
                                        '### Fictional project (Oct 2026)'])
                self.assertIsNone(ai.cv_heading_problem(content))
        self.assertIsNone(ai.cv_heading_problem('## Experience\n- Checked fictional records.'))

    def test_source_chunks_preserve_renderer_boundaries_and_original_text(self):
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=newline):
                content = newline.join(['# Fictional Name', '', '# Summary', 'Fictional analyst.',
                                        '---', '## Experience', '### ' + ROLE,
                                        '- Checked fictional records.', '# Skills', '- SQL', ''])
                head, sections = ai.cv_layout.parse(content)
                parts = ai.cv_source_sections(content)
                self.assertEqual(''.join(text for _, text in parts), content)
                self.assertEqual([name for name, _ in parts[1:]],
                                 [ai.cv_layout.section_heading('## ' + s['name']) for s in sections])
                self.assertTrue(all(set(s) == {'name', 'items'} for s in sections))
                self.assertEqual(head, ['# Fictional Name'])

    def test_schema_is_strict_and_accepts_summary_kind(self):
        claims = ai.schema('cv')['properties']['claims']['items']
        self.assertFalse(claims['additionalProperties'])
        self.assertEqual(claims['properties']['kind']['enum'], ['bullet', 'summary'])
        self.assertEqual(set(claims['required']), set(claims['properties']))

    def test_public_files_have_no_private_markers(self):
        paths = [HERE / 'ai_tasks.py', Path(__file__), APP / 'settings.example.json',
                 APP / 'docs/ARCHITECTURE.md', APP / '.github/workflows/tests.yml']
        paths += list((APP / 'evidence').glob('*.md'))
        # Keep private identity and marker lines out of public implementation files.
        forbidden = ['Signed' + ':', 'Car' + 'los', 'Red' + 'ondo', 'C:' + '\\Users\\']
        for path in paths:
            text = path.read_text(encoding='utf-8')
            for term in forbidden:
                self.assertNotIn(term.casefold(), text.casefold(), path.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
