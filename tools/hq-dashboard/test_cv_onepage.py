# Fictional fixtures and settings-aware expectations.
"""Offline checks for the CV trimmer and the CV length cap. Fictional text only."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


c = load('cv_onepage')
SOURCE = '# EXAMPLE PERSON\nTagline | Example\nCity | x@example.com\n\n## Summary\nFictional filler words one. Fictional filler words two. Fictional filler words three.\n\n## Experience\n' + ''.join(
    f'**Role {r} | Company {r}** | 2020 to 2021\n' + ''.join(f'- Fictional bullet {r}.{b} ' + 'filler words ' * 14 + '\n' for b in range(1, 7)) for r in range(1, 7)) + '\n## Skills\nOne, two, three\n'


class Trim(unittest.TestCase):
    def run_trim(self, source=SOURCE):
        head, sections = c.trim(source)
        return c.render_md(head, sections), head, sections

    def test_result_fits_and_is_verbatim(self):
        text, head, sections = self.run_trim()
        pages, last, _ = c.fill(head, sections)
        self.assertTrue(pages == 1 or c.MIN_LAST <= last, (pages, last))
        c.verify_subset(SOURCE, text)
        self.assertLess(len(text), len(SOURCE))

    def test_second_page_is_never_near_empty(self):
        # Sources from tiny to very long: the estimate gives one page, or a last page with at least MIN_LAST lines.
        for roles in range(1, 9):
            for bullets in (1, 3, 6):
                for filler in (4, 14, 30):
                    src = make_source(roles, bullets, filler)
                    with self.subTest(roles=roles, bullets=bullets, filler=filler):
                        text, head, sections = self.run_trim(src)
                        c.verify_subset(src, text)
                        pages, last, _ = c.fill(head, sections)
                        self.assertTrue(pages == 1 or last >= c.MIN_LAST, (pages, last))

    def test_design_caps_apply_even_when_it_fits(self):
        src = make_source(3, 6, 2)
        _, _, sections = self.run_trim(src)
        roles = c.entries(next(s for s in sections if s['name'] == 'Experience')['items'])
        self.assertEqual([len(r) - 1 for r in roles], [3, 3, 2])
        summary = next(s for s in sections if s['name'] == 'Summary')['items'][0]
        self.assertEqual(summary, 'Fictional filler words one. Fictional filler words two. Fictional filler words three.')

    def test_posting_related_bullets_survive_and_all_fit_one_page(self):
        src = make_source(5, 6, 14).replace('- Fictional bullet 1.6 ', '- Fictional bullet 1.6 reconciliation analyst ')
        focus = c.focus_words((c.SETTINGS['document_prefix'] + 'CV_Example_Reconciliation_Analyst.docx'))
        self.assertEqual(focus, {'example', 'reconciliation', 'analyst'})
        text, head, sections = (lambda hs: (c.render_md(*hs), *hs))(c.trim(src, focus))
        c.verify_subset(src, text)
        self.assertIn('Fictional bullet 1.6 reconciliation analyst', text)
        self.assertEqual(c.fill(head, sections)[0], 1)
        for name in ('Skills', 'Education', 'Languages'):
            self.assertIn('## ' + name, text)
        self.assertEqual(text.count('**Role '), 5)  # role title lines are never deleted

    def test_summary_never_claims_what_the_page_no_longer_shows(self):
        # The Zorblax claim is proven only by the last bullet of the oldest role, the first one any cut would take.
        claim = 'Trained twelve hires on Zorblax pipelines and cut rework by $40,000.'
        src = make_source(5, 3, 14).replace('Fictional filler words two. ', claim + ' ', 1)
        proof = '- Fictional bullet 5.3 trained twelve hires on Zorblax pipelines, cutting rework by $40,000.\n'
        src = src.replace('- Fictional bullet 5.3 ' + 'filler words ' * 14 + '\n', proof)
        text, head, sections = (lambda hs: (c.render_md(*hs), *hs))(c.trim(src))
        c.verify_subset(src, text)
        c.verify_summary_support(sections)
        self.assertEqual(c.fill(head, sections)[0], 1)
        self.assertEqual(claim in text, 'Zorblax pipelines, cutting' in text)  # claim kept only with its proof
        # The check in main() refuses a page whose Summary lost its proof.
        _, broken = c.parse(src.replace(proof, ''))
        with self.assertRaises(SystemExit):
            c.verify_summary_support(broken)
        self.assertFalse(c.unsupported_summary(c.parse('## Summary\nOperations analyst with 2+ years of work.\n')[1]))

    def test_ai_posting_keeps_the_agent_project_bullet(self):
        src = make_source(5, 3, 14).replace('- Project bullet 1 ', '- Project bullet 1 directed agents with review gates ')
        focus = c.focus_words((c.SETTINGS['document_prefix'] + 'CV_Example_Ai_Workflow_Engineer.docx'))
        text, head, sections = (lambda hs: (c.render_md(*hs), *hs))(c.trim(src, focus))
        c.verify_subset(src, text)
        self.assertIn('directed agents with review gates', text)
        self.assertEqual(c.fill(head, sections)[0], 1)
        roles = c.entries(next(s for s in sections if s['name'] == 'Experience')['items'])
        self.assertTrue(all(len(r) >= 2 for r in roles))  # never below 1 bullet per role

    def test_marker_lines_never_reach_the_output(self):
        src = SOURCE.replace('## Skills\n', '## Skills\nSigned\x3a Writer (Example), 2026-01-01. DRAFT ONLY.\n') + 'Sentiment: warm\n[DRAFT]\n'
        text, head, sections = self.run_trim(src)
        c.verify_subset(src, text)
        for marker in ('Signed', 'Sentiment', 'DRAFT'):
            self.assertNotIn(marker, text)
        with self.assertRaises(SystemExit):
            c.verify_subset(src, text + 'Signed\x3a Codex\n')
        with self.assertRaises(SystemExit):
            c.trim(SOURCE.replace('One, two, three', 'One, [DRAFT] two'))

    def test_reworded_or_truncated_lines_are_refused(self):
        for bad in ('- Fictional bullet 1.1 filler', '- A brand new claim.', '## Summary\nFictional filler'):
            with self.assertRaises(SystemExit):
                c.verify_subset(SOURCE, bad)

    def test_bullet_moved_under_another_role_is_refused(self):
        title = '**Role 2 | Company 2** | 2020 to 2021\n'
        moved = SOURCE.replace(title, title + '- Fictional bullet 1.1 ' + 'filler words ' * 14 + '\n', 1)
        with self.assertRaises(SystemExit):
            c.verify_subset(SOURCE, moved)

    def test_moved_sentences_are_refused(self):
        with self.assertRaises(SystemExit):
            c.verify_subset(SOURCE, '## Summary\nFictional filler words two. Fictional filler words one.')


def make_source(roles, bullets, filler):
    return ('# EXAMPLE PERSON\nTagline | Example\nCity | x@example.com | +1 555 0100\n\n## Summary\n'
            'Fictional filler words one. Fictional filler words two. Fictional filler words three. '
            'Fictional filler words four.\n\n## Projects\n'
            '**Fictional project (Oct 2026)**\n' + ''.join(f'- Project bullet {b} ' + 'filler ' * filler + '\n' for b in range(3)) +
            '\n## Experience\n' + ''.join(f'**Role {r} | Company {r}** | Jan 2020 to May 2021\n' + ''.join(
                f'- Fictional bullet {r}.{b} ' + 'filler words ' * filler + '\n' for b in range(1, bullets + 1)) for r in range(1, roles + 1)) +
            '\n## Skills\nOne, two, three\n\n## Education\nFictional degree, Example University\n\n## Languages\nExample: native.\n')


def words(text):
    return re.findall(r"[A-Za-z0-9$%+&@]+", text)


class WordFile(unittest.TestCase):
    """The .docx layout."""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory(prefix='cv-layout-test-')
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, src=None):
        import docx
        src = src or make_source(5, 3, 10)
        head, sections = c.trim(src)
        path = self.dir / 'cv.docx'
        c.build_docx(head, sections, path)
        return docx.Document(path), c.render_md(head, sections)

    def test_no_header_footer_tables_or_pictures(self):
        import zipfile
        d, _ = self.build()
        self.assertEqual(d.tables, [])
        for s in d.sections:
            self.assertTrue(s.header.is_linked_to_previous and s.footer.is_linked_to_previous)
        names = zipfile.ZipFile(self.dir / 'cv.docx').namelist()
        self.assertFalse([n for n in names if re.search(r'header|footer|media/|textbox', n)], names)
        self.assertNotIn('w:txbxContent', d.element.xml)
        self.assertNotIn('w:drawing', d.element.xml)

    def test_fonts_are_arial(self):
        d, _ = self.build()
        used = {p.style.name for p in d.paragraphs} | {'Normal'}
        for st in (d.styles[n] for n in used):
            rf = st.element.rPr.find(c.layout.qn('w:rFonts')) if st.element.rPr is not None else None
            with self.subTest(style=st.name):
                self.assertIsNotNone(rf)
                self.assertEqual(rf.get(c.layout.qn('w:ascii')), 'Arial')
                self.assertFalse([k for k in rf.attrib if k.endswith('Theme')])
        self.assertFalse([r for p in d.paragraphs for r in p.runs if r.font.name not in (None, 'Arial')])
        sizes = [p.style.font.size.pt for p in d.paragraphs if p.style.name in ('CV Body', 'CV Bullet')]
        self.assertTrue(sizes and min(sizes) >= 10)

    def test_headings_have_a_bottom_rule_and_dates_a_right_tab(self):
        d, md = self.build()
        heads = [p for p in d.paragraphs if p.style.name == 'CV Section']
        self.assertEqual([p.text for p in heads], ['Summary', 'Projects', 'Experience', 'Skills', 'Education', 'Languages'])
        qn = c.layout.qn
        for p in heads:
            self.assertIsNotNone(p.style.element.pPr.find(qn('w:pBdr')).find(qn('w:bottom')))
        roles = [p for p in d.paragraphs if p.style.name == 'CV Role']
        self.assertEqual(len(roles), 1 + md.count('| Jan 2020 to May 2021'))
        tab = d.styles['CV Role'].paragraph_format.tab_stops[0]
        self.assertEqual(str(tab.alignment), 'RIGHT (2)')
        self.assertEqual(tab.position, c.layout.Twips(c.layout.PAGE_W - 2 * c.layout.MARGIN_X))
        self.assertTrue(all('\t' in p.text for p in roles))
        self.assertEqual(roles[0].text, 'Fictional project\tOct 2026')
        self.assertTrue(d.styles['CV Role'].font.bold)  # the title inherits bold; the company is set regular
        self.assertEqual((roles[1].runs[0].bold, roles[1].runs[1].bold), (None, False))
        bullets = [p for p in d.paragraphs if p.style.name == 'CV Bullet']
        self.assertTrue(bullets and not any(p.text.startswith(('-', '•')) for p in bullets))

    def test_docx_words_equal_markdown_words(self):
        d, md = self.build()
        self.assertEqual(words('\n'.join(p.text for p in d.paragraphs)), words(md))
        d, md = self.build(make_source(2, 2, 3).replace('**Role 1 | Company 1** | Jan 2020 to May 2021',
                                                         '**Role 1 | Company 1 | Jan 2020 to May 2021**'))
        self.assertEqual(words('\n'.join(p.text for p in d.paragraphs)), words(md))

    def test_only_separators_and_tabs_are_new_characters(self):
        d, md = self.build()
        text = '\n'.join(p.text for p in d.paragraphs)
        self.assertEqual(set(text) - set(md), {'·', '\t'})
        self.assertIn('x@example.com · +1 555 0100', text)

    def test_page_two_is_whole_sections_when_needed(self):
        import docx
        src = make_source(5, 6, 26)
        head, sections = c.trim(src)
        pages, last, _ = c.fill(head, sections)
        self.assertTrue(pages == 1 or last >= c.MIN_LAST, (pages, last))
        c.build_docx(head, sections, self.dir / 'two.docx')
        breaks = [p for p in docx.Document(self.dir / 'two.docx').paragraphs if p.paragraph_format.page_break_before]
        self.assertTrue(all(p.style.name == 'CV Section' for p in breaks))
        self.assertLessEqual(len(breaks), 1)
        self.assertNotIn('page_break', c.render_md(head, sections))

    def test_drafted_cv_export_uses_the_same_style(self):
        import docx
        conv = load('docx_export')
        path = self.dir / 'draft.docx'
        conv.md_to_docx('Signed\x3a Codex\n' + make_source(2, 2, 3), path)
        d = docx.Document(path)
        self.assertEqual(d.paragraphs[0].style.name, 'CV Name')
        self.assertIn('CV Section', {p.style.name for p in d.paragraphs})
        self.assertNotIn('Signed', '\n'.join(p.text for p in d.paragraphs))


class WordCap(unittest.TestCase):
    def test_markdown_symbols_do_not_count(self):
        ai = load('ai_tasks')
        text = ' '.join(['- **word**'] * 550) + ' | | | ' * 100
        self.assertEqual(ai.word_count(text), 550)
        self.assertGreater(ai.word_count('word ' * 601), 600)


if __name__ == '__main__':
    unittest.main()
