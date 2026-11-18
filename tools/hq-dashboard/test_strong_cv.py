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
PROJECT = 'Fictional Agent Review'
BANK = {'rubric_version': ai.RUBRIC_VERSION, 'rules': 'Fictional only.', 'entries': {
    'E-001': {'text': 'Built 3 reports with SQL.', 'roles': [ROLE], 'skills': ['Reporting', 'SQL', 'Python']},
    'E-002': {'text': 'Checked fictional records.', 'roles': [ROLE], 'skills': ['sql', 'Record checks']},
    'E-003': {'text': 'Directed fictional agents.', 'skills': ['Agent design'], 'project': PROJECT},
}}
MATCHES = [
    {'requirement': 'SQL reporting', 'importance': 'must', 'level': 'strong', 'evidence': 'E-001'},
    {'requirement': 'Record checks', 'importance': 'nice', 'level': 'partial', 'evidence': 'E-002'},
    {'requirement': 'Automation', 'importance': 'must', 'level': 'partial', 'evidence': 'E-001, E-003'},
    {'requirement': 'Missing skill', 'importance': 'must', 'level': 'gap', 'evidence': 'E-002'},
    {'requirement': 'Reports', 'importance': 'nice', 'level': 'strong', 'evidence': 'E-001'},
    {'requirement': 'Data', 'importance': 'nice', 'level': 'partial', 'evidence': 'E-001'},
]
LEGACY_CV = ('Fictional analyst\n## Summary\nBuilt 3 reports with SQL.\n## Experience\n### ' + ROLE +
      '\n- Checked fictional records.\n- Built 3 reports with SQL.\n## Projects\n- Directed fictional agents.\n'
      '## Skills\n- Invented skill\n## Education\nFictional training.\n')
CV = LEGACY_CV.replace('## Projects\n', '## Projects\n### ' + PROJECT + '\n')
CLAIMS = [{'text': e['text'], 'evidence': [key]} for key, e in BANK['entries'].items()]
SUMMARY_CLAIMS = [{'text': text, 'kind': 'summary', 'evidence': ['E-001']}
                  for text in ('Fictional analyst', 'Built 3 reports with SQL.')]
HEADING_PROBLEM = 'The CV used a heading HQ does not accept (use ## for sections and ### for jobs). Try again.'


class SkillsBank(unittest.TestCase):
    def test_model_project_metadata_and_parenthesized_skills(self):
        with tempfile.TemporaryDirectory(prefix='hq-model-bank-') as temp:
            root = Path(temp)
            for name in ('settings.example.json', 'rules.example.md'):
                shutil.copyfile(APP / name, root / name)
            (root / 'evidence').mkdir()
            for index, title in enumerate(['Fictional Agent Review', '', 'A' * 81], 1):
                (root / 'evidence' / f'E-{index:03}-fictional.md').write_text(
                    '# Fictional\n- level: stated\n- public-safe: yes\n'
                    '- CV-ready bullet: Directed fictional agents.\n'
                    '- skills: Spanish (native), English (B2)\n- project: ' + title + '\n', encoding='utf-8')
            bank = ai.evidence_bank(root)
            self.assertEqual(bank['entries']['E-001']['project'], 'Fictional Agent Review')
            self.assertEqual(bank['entries']['E-001']['skills'], ['Spanish (native)', 'English (B2)'])
            self.assertEqual([e['id'] for e in bank['needs_fixing']], ['E-002', 'E-003'])

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
            'owner_links': [], 'cv_skills_placement': 'end',
            'cv_fixed_sections': [],
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

    def test_model_links_are_site_owned_and_word_hyperlinks(self):
        import io
        import docx
        links = [{'label': 'Portfolio', 'text': 'example.github.io', 'url': 'https://example.github.io'},
                 {'label': 'LinkedIn', 'text': '', 'url': 'https://example.com/profile'}]
        with mock.patch.dict(ai.SETTINGS, {'owner_links': links}):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        lines = fit['cv_markdown'].splitlines()
        self.assertEqual(lines[:4], ['# Alex Example', 'Fictional analyst', 'alex@example.com',
            '[Portfolio: example.github.io](https://example.github.io) · [LinkedIn](https://example.com/profile)'])
        for prompt in self.prompts:
            self.assertNotIn('example.github.io', prompt)
            self.assertNotIn('https://example.com/profile', prompt)
        stream = io.BytesIO()
        ai.cv_layout.md_to_docx(fit['cv_markdown'], stream)
        document = docx.Document(stream)
        paragraph = next(p for p in document.paragraphs if p.style.name == 'CV Links')
        self.assertEqual(len(paragraph._p.findall(ai.cv_layout.qn('w:hyperlink'))), 2)
        self.assertEqual({r.target_ref for r in document.part.rels.values() if r.reltype.endswith('/hyperlink')},
                         {link['url'] for link in links})

    def test_model_tools_rule_counts_must_requirements_and_orders_sections(self):
        sections = dict(ai.cv_source_sections(CV))
        self.output['cv_markdown'] = ''.join(sections[name] for name in
            (None, 'education', 'projects', 'experience', 'summary', 'skills'))
        matches = [{'importance': 'must', 'requirement': 'SQL and Python'},
                   {'importance': 'must', 'requirement': 'Python imports'}]
        with mock.patch.dict(ai.SETTINGS, {'cv_skills_placement': 'tools_tested'}):
            for requirements, shown in [(matches[:1], False), (matches, True),
                    ([matches[0], dict(matches[1], importance='nice')], False), ([], False)]:
                task, fit = self.draft(matches=requirements)
                self.assertEqual(task['status'], 'done', task['message'])
                names = [s['name'] for s in ai.cv_layout.parse(fit['cv_markdown'])[1]]
                self.assertEqual(names, ['Summary'] + (['Technical Skills'] if shown else []) +
                                 ['Experience', 'Selected Projects', 'Education'])
                self.assertNotIn('## Skills', fit['cv_markdown'])

    def test_model_experience_requires_exact_role_evidence_and_claimed_prose(self):
        self.output.update(sources=['E-001'], claims=[CLAIMS[0]])
        experience = '## Experience\n### ' + ROLE + '\n- Built 3 reports with SQL.\n'
        older = 'Support | Sample Air | Jan 2020 to Apr 2021'
        for heading, prose, status in (
                (older, 'Managed payroll for 80 employees.\n', 'failed'),
                (older, '', 'failed'),
                (ROLE.replace('Analyst', 'analyst'), '', 'failed'),
                (ROLE, 'Managed payroll for 80 employees.\n', 'failed'),
                (ROLE, '', 'done')):
            with self.subTest(heading=heading, prose=prose):
                self.output['cv_markdown'] = experience + '### ' + heading + '\n' + prose
                task, fit = self.draft()
                self.assertEqual(task['status'], status, task['message'])
                if status == 'done':
                    self.assertIn('### ' + heading + '\n## Skills', fit['cv_markdown'])
                else:
                    self.assertNotIn('cv_markdown', fit)

    def test_model_projects_reject_untitled_bullet(self):
        self.output['cv_markdown'] = LEGACY_CV
        task, fit = self.draft()
        self.assertEqual(task['status'], 'failed', task['message'])
        self.assertNotIn('cv_markdown', fit)

    def test_model_projects_reject_swapped_titles(self):
        first = PROJECT
        second = 'Fictional Record Audit'
        first_bullet = '- Directed fictional agents.\n'
        second_bullet = '- Checked fictional records.\n'
        self.output.update(sources=['E-003', 'E-002'], claims=[CLAIMS[2], CLAIMS[1]])
        with mock.patch.dict(BANK['entries']['E-003'], {'project': first}), \
                mock.patch.dict(BANK['entries']['E-002'], {'project': second}):
            for projects, status in (
                    ('### ' + second + '\n' + first_bullet + '### ' + first + '\n' + second_bullet, 'failed'),
                    ('### ' + first + '\n' + first_bullet + '### ' + second + '\n' + second_bullet, 'done')):
                with self.subTest(projects=projects):
                    self.output['cv_markdown'] = '## Selected Projects\n' + projects
                    task, fit = self.draft()
                    self.assertEqual(task['status'], status, task['message'])
                    if status == 'failed':
                        self.assertNotIn('cv_markdown', fit)

    def test_model_links_export_as_word_hyperlinks_without_owner_name(self):
        import io
        import docx
        links = [{'label': 'Portfolio', 'text': '', 'url': 'https://example.org'}]
        with mock.patch.dict(ai.SETTINGS, {'owner_name': '', 'owner_contact': '', 'owner_links': links}):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        stream = io.BytesIO()
        ai.cv_layout.md_to_docx(fit['cv_markdown'], stream)
        document = docx.Document(stream)
        paragraphs = [p for p in document.paragraphs if p.style.name == 'CV Links']
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(len(paragraphs[0]._p.findall(ai.cv_layout.qn('w:hyperlink'))), 1)
        self.assertEqual({r.target_ref for r in document.part.rels.values() if r.reltype.endswith('/hyperlink')},
                         {'https://example.org'})

    def test_model_project_title_matches_cited_entry(self):
        with mock.patch.dict(BANK['entries']['E-003'], {'project': 'Fictional Agent Review'}):
            for title, cited, status in [('Fictional Agent Review', True, 'done'),
                                         ('Invented project', True, 'failed'),
                                         ('Fictional Agent Review', False, 'failed')]:
                self.output['cv_markdown'] = LEGACY_CV.replace('## Projects\n', '## Selected Projects\n### ' + title + '\n')
                self.output['sources'] = list(BANK['entries']) if cited else ['E-001', 'E-002']
                self.output['claims'] = CLAIMS + SUMMARY_CLAIMS if cited else CLAIMS[:2] + SUMMARY_CLAIMS
                task, fit = self.draft()
                self.assertEqual(task['status'], status, task['message'])
                if status == 'failed':
                    self.assertIn('project title', task['message'])
                else:
                    plan = ai.cv_layout.blocks(*ai.cv_layout.parse(fit['cv_markdown']))
                    self.assertIn(('CV Role', (title, '', '')), plan)

    def test_model_oldest_role_can_have_no_bullets(self):
        older = 'Support | Sample Air | Jan 2020 to Apr 2021'
        with mock.patch.dict(BANK['entries']['E-002'], {'roles': [older]}):
            self.output['cv_markdown'] = CV.replace('## Projects', '### ' + older + '\n## Projects')
            self.output['claims'] = CLAIMS + SUMMARY_CLAIMS + [{'text': older, 'evidence': ['E-002']}]
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('### ' + older + '\n## Selected Projects', fit['cv_markdown'])
        self.assertNotIn('Jobs are not in newest-first order.', fit['cv_flags'])

    def test_model_fixed_lines_join_text_and_language_skills(self):
        self.fixed_sections()
        with mock.patch.dict(BANK['entries']['E-005'], {'skills': ['Spanish (native)', 'English (B2)']}), \
                mock.patch.dict(ai.SETTINGS, {'cv_fixed_sections': [{'name': 'Education and Languages', 'lines': [
                    {'entries': ['E-004', 'E-005'], 'from': 'text', 'join': ' | '},
                    {'entries': ['E-005'], 'from': 'skills', 'join': ' | '}]}]}):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertTrue(fit['cv_markdown'].endswith('## Education and Languages\n'
            'Diploma in fictional studies. | Speaks fictional languages.\nSpanish (native) | English (B2)\n'))

    def test_model_rationale_saved_separately_and_bounded(self):
        self.assertIn('rationale', ai.schema('cv')['required'])
        rationale = 'The posting prioritizes fictional record checks and SQL reporting. ' * 10
        self.output['rationale'] = rationale
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertEqual(fit['cv_rationale'], rationale)
        self.assertNotIn(rationale, fit['cv_markdown'])
        for value in ('Too short.', 'word ' * 351):
            self.output['rationale'] = value
            task, _ = self.draft()
            self.assertEqual(task['status'], 'failed')
            self.assertIn('rationale', task['message'])

    def test_model_notes_export_and_legacy_export(self):
        files_spec = importlib.util.spec_from_file_location('model_pc_files', HERE / 'pc_files.py')
        files = importlib.util.module_from_spec(files_spec)
        files_spec.loader.exec_module(files)
        rationale = 'Fictional scope is limited to the checked evidence. ' * 10
        with tempfile.TemporaryDirectory(prefix='hq-model-export-') as temp:
            root = Path(temp)
            record = {'company': 'Example', 'role': 'Analyst', 'fit': {'cv_markdown': LEGACY_CV, 'cv_rationale': rationale}}
            posting_id = str(uuid.uuid4())
            path = files.export_draft(root / 'state', posting_id, 'cv', record, work=root / 'work')
            notes = path.with_name(path.stem + '_notes.md')
            self.assertIn(rationale, notes.read_text(encoding='utf-8'))
            self.assertIn('Date made:', notes.read_text(encoding='utf-8'))
            item = next(r for r in files.reports(root / 'work') if r['name'] == notes.name)
            self.assertEqual(item['cv_id'], files._key(path.name))
            self.assertEqual(files.report_file(item['id'], root / 'work')[0], notes)
            self.assertEqual(files.export_draft(root / 'state', posting_id, 'cv', record, work=root / 'work'), path)
            self.assertEqual(len(list((root / 'work').glob('*_notes.md'))), 1)
            report = root / 'work' / 'report-fictional_notes.md'
            report.write_text('Fictional report.', encoding='utf-8')
            item = next(r for r in files.reports(root / 'work') if r['name'] == report.name)
            self.assertNotIn('cv_id', item)
            self.assertEqual(files.report_file(item['id'], root / 'work')[0], report)

            target = root / 'copies'
            dest = target / files.SETTINGS['copy_subfolder']
            dest.mkdir(parents=True)
            older = dest / path.name
            older.write_bytes(b'Older fictional CV')
            copied = dest / (path.stem + '_2.docx')
            copied_notes = copied.with_name(copied.stem + '_notes.md')
            for _ in range(2):
                files.export_draft(root / 'state', posting_id, 'cv', record, copy_dir=target, work=root / 'work')
                self.assertEqual(older.read_bytes(), b'Older fictional CV')
                self.assertEqual(copied.read_bytes(), path.read_bytes())
                self.assertEqual(copied_notes.read_bytes(), notes.read_bytes())
                self.assertEqual(set(dest.iterdir()), {older, copied, copied_notes})

            record['fit'].pop('cv_rationale')
            record['company'] = 'Legacy'
            old = files.export_draft(root / 'state', str(uuid.uuid4()), 'cv', record, work=root / 'work')
            self.assertTrue(old.is_file())
            self.assertFalse(old.with_name(old.stem + '_notes.md').exists())

    def test_skills_are_built_from_cited_entries(self):
        self.output['cv_markdown'] = CV.split('## Projects')[0] + '## Education\nFictional training.\n'
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

    def fixed_sections(self):
        entries = {
            'E-004': {'text': 'Diploma in fictional studies.', 'skills': ['Study planning']},
            'E-005': {'text': 'Speaks fictional languages.', 'skills': ['English', 'Spanish']},
        }
        for patch in (mock.patch.dict(BANK['entries'], entries), mock.patch.dict(ai.SETTINGS, {
                'cv_fixed_sections': [
                    {'name': 'Education', 'entries': ['E-004'], 'from': 'text'},
                    {'name': 'Languages', 'entries': ['E-005'], 'from': 'skills'}]})):
            patch.start()
            self.addCleanup(patch.stop)

    def test_fixed_sections_added_when_ai_omits_both_and_evidence_map_passes(self):
        self.fixed_sections()
        self.output['cv_markdown'] = CV.split('## Education')[0]
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        cv = fit['cv_markdown']
        self.assertTrue(cv.endswith('## Education\nDiploma in fictional studies.\n'
                                    '## Languages\nEnglish, Spanish\n'))
        self.assertLess(cv.index('## Skills'), cv.index('## Education'))
        self.assertEqual(fit['sources'][-2:], ['E-004', 'E-005'])
        for text, evidence in [('Diploma in fictional studies.', ['E-004']),
                               ('English, Spanish', ['E-005'])]:
            self.assertTrue(any(c['text'] == text and c['evidence'] == evidence for c in fit['cv_claims']))
        self.assertTrue(all(c['text'] in cv and all(s in fit['sources'] for s in c['evidence'])
                            for c in fit['cv_claims']))

    def test_fixed_sections_replace_ai_text_and_its_obsolete_claims(self):
        self.fixed_sections()
        for heading in ('## Education', '##\t eDuCaTiOn :  '):
            with self.subTest(heading=heading):
                self.output['cv_markdown'] = CV.split('## Education')[0] + heading + '\nChecked fictional records.\n'
                # This exact claim appears only in the section being replaced.
                self.output['cv_markdown'] = self.output['cv_markdown'].replace('- Checked fictional records.\n', '')
                task, fit = self.draft()
                self.assertEqual(task['status'], 'done', task['message'])
                self.assertEqual(fit['cv_markdown'].count('## Education'), 1)
                self.assertNotIn('Checked fictional records.', fit['cv_markdown'])
                self.assertFalse(any(c['text'] == 'Checked fictional records.' for c in fit['cv_claims']))
                self.assertIn('## Selected Projects\n### ' + PROJECT + '\n- Directed fictional agents.', fit['cv_markdown'])

    def test_fixed_entry_skills_are_excluded_from_general_skills(self):
        self.fixed_sections()
        self.output['sources'] += ['E-004', 'E-005']
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        skills = next(s['items'] for s in ai.cv_layout.parse(fit['cv_markdown'])[1] if s['name'] == 'Skills')
        self.assertNotIn('Study planning', '\n'.join(skills))
        self.assertNotIn('English', '\n'.join(skills))
        self.assertEqual(fit['cv_markdown'].count('English'), 1)

    def test_fixed_sections_skip_unknown_entries_and_empty_sections(self):
        self.fixed_sections()
        with mock.patch.dict(ai.SETTINGS, {'cv_fixed_sections': [
                {'name': 'Education', 'entries': ['E-999', 'E-004'], 'from': 'text'},
                {'name': 'Languages', 'entries': ['E-999'], 'from': 'skills'},
                {'name': 'Training', 'entries': [], 'from': 'text'}]}):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('Diploma in fictional studies.', fit['cv_markdown'])
        self.assertNotIn('## Languages', fit['cv_markdown'])
        self.assertNotIn('## Training', fit['cv_markdown'])
        self.assertNotIn('E-999', fit['sources'])

    def test_empty_fixed_setting_keeps_existing_education_and_claims(self):
        task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn('## Education\nFictional training.', fit['cv_markdown'])
        self.assertEqual(fit['sources'], list(BANK['entries']))
        expected_claims = CLAIMS + [
            {**claim, 'kind': 'bullet'}
            if claim['text'] == 'Built 3 reports with SQL.' and claim['evidence'] == ['E-001']
            else claim for claim in SUMMARY_CLAIMS
        ]
        self.assertEqual(fit['cv_claims'], expected_claims)

    def test_fixed_sections_follow_setting_order_and_listed_entry_order(self):
        self.fixed_sections()
        with mock.patch.dict(ai.SETTINGS, {'cv_fixed_sections': [
                {'name': 'Languages', 'entries': ['E-005', 'E-004'], 'from': 'skills'},
                {'name': 'Education', 'entries': ['E-005', 'E-004'], 'from': 'text'}]}):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertTrue(fit['cv_markdown'].endswith(
            '## Languages\nEnglish, Spanish, Study planning\n## Education\n'
            'Speaks fictional languages.\nDiploma in fictional studies.\n'))

    def test_fixed_sections_still_obey_hard_word_limit(self):
        self.fixed_sections()
        self.output.update(cv_markdown='## Experience\n- Built 3 reports with SQL.\n' + 'word ' * 580,
                           sources=['E-001'], claims=CLAIMS[:1])
        with mock.patch.dict(BANK['entries'], {'E-004': {'text': 'Fictional study ' * 20}}):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('too long', task['message'])
        self.assertNotIn('cv_markdown', fit)

    def test_fixed_skills_with_no_skills_removes_ai_section(self):
        self.fixed_sections()
        self.output['cv_markdown'] += '## Languages\nFictional AI languages.\n'
        with mock.patch.dict(BANK['entries'], {'E-005': {'text': 'Fictional language record.'}}):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertNotIn('## Languages', fit['cv_markdown'])
        self.assertNotIn('Fictional AI languages.', fit['cv_markdown'])

    def test_fixed_section_claims_are_checked_after_transform(self):
        self.fixed_sections()
        transform = ai.strong_cv
        with mock.patch.object(ai, 'strong_cv', side_effect=lambda *args:
                               transform(*args).replace('English, Spanish', 'English')):
            task, fit = self.draft()
        self.assertEqual(task['status'], 'failed')
        self.assertIn('evidence map', task['message'])
        self.assertNotIn('cv_markdown', fit)

    def test_no_skills_means_no_section_and_no_education_means_append(self):
        with mock.patch.dict(BANK['entries'], {'E-001': {'text': BANK['entries']['E-001']['text']}}):
            self.output.update(cv_markdown='## Experience\n- Built 3 reports with SQL.\n',
                               sources=['E-001'], claims=CLAIMS[:1])
            task, fit = self.draft()
            self.assertEqual(task['status'], 'done', task['message'])
            self.assertNotIn('## Skills', fit['cv_markdown'])
        self.output['cv_markdown'] = '## Experience\n### ' + ROLE + '\n- Built 3 reports with SQL.\n'
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
        self.assertTrue(fit['cv_markdown'].startswith('# Alex Example\nAnalyst with SQL\nalex@example.com'))

    def test_headline_can_use_posting_words_and_cited_project_words(self):
        headline = 'Data Integration | Excel and Validation'
        self.output.update(cv_markdown=headline, sources=['E-003'],
                           claims=[{'text': headline, 'kind': 'summary', 'evidence': ['E-003']}])
        with mock.patch.dict(BANK['entries'], {'E-003': dict(BANK['entries']['E-003'], project='Import Validation')}):
            task, fit = self.draft(title='Data Excel analyst', text='Work on integration and imports.')
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertIn(headline, fit['cv_markdown'].splitlines())

    def test_posting_words_do_not_support_headline_numbers_or_bank_skills(self):
        for headline, token in [('10 Years | Data', '10'), ('Data | Agent Design', 'Agent design')]:
            with self.subTest(headline=headline):
                self.output.update(cv_markdown=headline, sources=['E-001'],
                                   claims=[{'text': headline, 'kind': 'summary', 'evidence': ['E-001']}])
                task, _ = self.draft(title='Data analyst', text='10 years of data work and agent design.')
                self.assertEqual(task['status'], 'failed')
                self.assertIn(f'"{token}"'.lower(), task['message'].lower())
                self.assertIn('cited evidence', task['message'])

    def test_posting_words_do_not_support_summary_capitals(self):
        headline = 'Data Integration | Excel and Validation'
        for sentence in ('Handled Integration checks.', headline):
            with self.subTest(sentence=sentence):
                self.output.update(cv_markdown=f'{headline}\n## Summary\n{sentence}', sources=['E-001'],
                                   claims=[{'text': text, 'kind': 'summary', 'evidence': ['E-001']}
                                           for text in dict.fromkeys((headline, sentence))])
                task, _ = self.draft(title='Data analyst', text='Work on integration, excel and validation.')
                self.assertEqual(task['status'], 'failed')
                self.assertIn('"Integration"', task['message'])
                self.assertIn('cited evidence', task['message'])

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
        for heading in ('', '## Summary\n', '## Experience\n', '## Projects\n### ' + PROJECT + '\n'):
            with self.subTest(heading=heading):
                key = 'E-003' if heading.startswith('## Projects') else 'E-002'
                text = BANK['entries'][key]['text']
                self.output.update(cv_markdown=heading + '- ' + text,
                                   sources=[key], claims=[{'text': text, 'kind': 'summary', 'evidence': [key]}])
                task, fit = self.draft()
                self.assertEqual(task['status'], 'done', task['message'])
                self.assertIn('- ' + text, fit['cv_markdown'])

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

    def test_ai_role_title_or_must_keeps_experience_before_projects(self):
        for title, matches in [('AI analyst', []), ('Fictional analyst', MATCHES)]:
            task, fit = self.draft(matches=matches, title=title)
            self.assertEqual(task['status'], 'done', task['message'])
            self.assertLess(fit['cv_markdown'].index('## Experience'), fit['cv_markdown'].index('## Selected Projects'))

    def test_non_ai_nice_and_substrings_leave_order(self):
        matches = [{'importance': 'nice', 'requirement': 'AI'}]
        task, fit = self.draft(matches=matches, title='Retail analyst', text='AI automation')
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertLess(fit['cv_markdown'].index('## Experience'), fit['cv_markdown'].index('## Selected Projects'))

    def test_old_record_has_zero_scores_and_keeps_bullet_order(self):
        self.assertTrue(all(e['score'] == 0 for e in ai.cv_evidence({}, BANK).values()))
        task, fit = self.draft(matches=None)
        self.assertEqual(task['status'], 'done', task['message'])
        self.assertLess(fit['cv_markdown'].index('- Checked'), fit['cv_markdown'].index('- Built 3'))

    def test_sort_is_stable_and_stays_inside_each_role(self):
        first = 'Checked fictional records.'
        second = 'Built 3 reports with SQL.'
        older = 'Support | Sample Air | Jan 2021 to Dec 2022'
        text = ('## Experience\n### ' + ROLE + '\n- ' + first + '\n- ' + second +
                '\n### ' + older + '\n- ' + first + '\n- ' + second)
        self.output['cv_markdown'] = text
        self.output['claims'] = CLAIMS[:2]
        roles = mock.patch.dict(BANK['entries']['E-002'], {'roles': [ROLE, older]})
        roles.start()
        self.addCleanup(roles.stop)
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
                           '## Projects\n### ' + PROJECT + '\n- Directed fictional agents.',
                           sources=['E-002', 'E-003'], claims=CLAIMS[1:])
        task, fit = self.draft(title='AI analyst')
        self.assertEqual(task['status'], 'done', task['message'])
        head, sections = ai.cv_layout.parse(fit['cv_markdown'])
        self.assertEqual(head, ['# Alex Example', 'alex@example.com'])
        self.assertEqual([s['name'] for s in sections], ['Experience', 'Selected Projects', 'Skills'])
        self.assertEqual(sections[1]['items'], ['### ' + PROJECT, '- Directed fictional agents.'])
        self.assertEqual(sections[0]['items'], ['- Checked fictional records.'])

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
                self.assertEqual(names, ['summary', 'experience', 'selected projects', 'skills', 'education'])

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
    def test_model_settings_validation_and_private_header(self):
        valid = {'owner_links': [{'label': 'LinkedIn', 'text': '', 'url': 'https://example.com/alex'}],
                 'cv_skills_placement': 'tools_tested', 'cv_tools_tested_min': 2, 'cv_tool_groups': ['Data'],
                 'cv_fixed_sections': [{'name': 'Education and Languages', 'lines': [
                     {'entries': ['E-003'], 'from': 'skills', 'join': ' | '}]}]}
        invalid = [('owner_links', [{'label': 'Portfolio', 'text': '', 'url': 'javascript:alert(1)'}]),
                   ('owner_links', [{'label': 'Bad\nlabel', 'text': '', 'url': 'https://example.com'}]),
                   ('cv_skills_placement', 'start'), ('cv_tools_tested_min', True),
                   ('cv_tools_tested_min', 0), ('cv_tool_groups', 'Data'),
                   ('cv_fixed_sections', [{'name': 'Education', 'lines': [
                       {'entries': ['E-003'], 'from': 'skills', 'join': '\n## Skills'}]}])]
        with tempfile.TemporaryDirectory(prefix='hq-model-settings-') as temp:
            root = Path(temp)
            shutil.copyfile(APP / 'settings.example.json', root / 'settings.example.json')
            (root / 'settings.json').write_text(json.dumps(valid), encoding='utf-8')
            loaded = ai.hq_settings.load(root)
            self.assertTrue(all(loaded[key] == value for key, value in valid.items()))
            self.assertNotIn('owner_links', ai.hq_settings.browser(root))
            for key, value in invalid:
                with self.subTest(key=key, value=value):
                    (root / 'settings.json').write_text(json.dumps(dict(valid, **{key: value})), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, key):
                        ai.hq_settings.load(root)

    def test_fixed_section_settings_shape(self):
        valid = [{'name': 'Education', 'entries': ['E-004'], 'from': 'text'},
                 {'name': 'Languages', 'entries': [], 'from': 'skills'}]
        invalid = [None, {}, [None], [{}],
                   [{'name': '', 'entries': [], 'from': 'text'}],
                   [{'name': 'Education\n## Other', 'entries': [], 'from': 'text'}],
                   [{'name': 'Education', 'entries': 'E-004', 'from': 'text'}],
                   [{'name': 'Education', 'entries': [4], 'from': 'text'}],
                   [{'name': 'Education', 'entries': ['unknown'], 'from': 'text'}],
                   [{'name': 'Education', 'entries': [], 'from': 'roles'}],
                   [dict(valid[0], extra=True)],
                   [valid[0], dict(valid[0], name=' education: ')],
                   [{'name': 'Skills', 'entries': [], 'from': 'text'}]]
        with tempfile.TemporaryDirectory(prefix='hq-fixed-settings-') as temp:
            root = Path(temp)
            shutil.copyfile(APP / 'settings.example.json', root / 'settings.example.json')
            for value in [[], valid] + invalid:
                with self.subTest(value=value):
                    (root / 'settings.json').write_text(json.dumps({'cv_fixed_sections': value}), encoding='utf-8')
                    if value in ([], valid):
                        self.assertEqual(ai.hq_settings.load(root)['cv_fixed_sections'], value)
                    else:
                        with self.assertRaisesRegex(ValueError, 'cv_fixed_sections'):
                            ai.hq_settings.load(root)

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
