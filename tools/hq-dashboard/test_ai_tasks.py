# Fictional fixtures and settings-aware expectations.
"""Offline career task tests with fictional evidence.
Activation updates: Codex - Activation Builder, 2026-11-07.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('pc_ai_fixture',HERE/'ai_tasks.py')
ai=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai)


from lead_policy_fixtures import fictional_analysis


class CareerTasks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hq-ai-test-')
        self.state=Path(self.temp.name)
        self.calls=[]
        self.bank={'rubric_version':'v3.2','rules':'Fictional evidence only.','entries':{'E-999':{'level':'stated','text':'Fictional example skill.'}}}
        self.result={'is_job_posting':True,'readable':True,'company':'Example Company','role':'Example Role','link':'https://example.com',
                     'posting_text':'','rubric_version':'v3.2','analysis':fictional_analysis(),'summary':'Fictional match.','location_ok':'unclear',
                     'matches':[{'requirement':'Example skill','evidence':'E-999','importance':'must','level':'partial'}],'gaps':[],'flags':[]}
        def fake(prompt,kind,folder,images):
            self.calls.append((prompt,kind,images))
            # Existing evidence-sanitization cases intentionally vary match fields.
            for m in self.result.get('matches',[]):
                m.setdefault('clause','Fictional posting clause')
                m.setdefault('rationale','Fictional evidence scope')
                m.setdefault('unsupported_scope','Fictional unsupported scope')
            return self.result
        self.runner=fake
        self.links=[]
        self.link_text=None
        def reader(link):
            self.links.append(link)
            return self.link_text
        self.tasks=ai.Tasks(self.state,runner=fake,bank_loader=lambda:self.bank,link_reader=reader)

    def tearDown(self):
        self.tasks.stop()
        self.temp.cleanup()

    def analyze(self):
        p={'kind':'analyze','requestId':str(uuid.uuid4()),'posting':{'company':'Example Company','role':'Example Role','link':'https://example.com','text':'Fictional posting requirements. '*10},'images':[]}
        key=self.tasks.submit(p)
        self.assertEqual(self.tasks.submit(p),key)
        self.tasks.process_one()
        self.assertEqual(self.tasks.list_tasks()[0]['status'],'done')
        return key

    def test_analyze_first_and_one_call(self):
        key=self.analyze()
        self.assertEqual(len(self.calls),1)
        self.assertFalse(self.tasks.posting(key)['reviewed'])
        self.assertNotIn('cv_markdown',self.tasks.posting(key)['fit'])
        self.assertIn('No tools, commands',self.calls[0][0])
        self.assertIn('E-999',self.calls[0][0])
        fit=self.tasks.posting(key)['fit']
        self.assertEqual(fit['rubric_version'],'v3.2')
        self.assertNotIn('score',fit)
        self.assertNotIn('verdict',fit)
        self.assertEqual(fit['want'],'Not set')

    def test_old_model_version_fails_without_posting(self):
        self.result['rubric_version']='v1'
        key=self.tasks.submit({'kind':'analyze','requestId':str(uuid.uuid4()),'posting':{'text':'Fictional posting '*30}})
        self.tasks.process_one()
        self.assertEqual(self.tasks.list_tasks()[0]['status'],'failed')
        self.assertEqual(self.tasks.postings(),[])

    def test_old_bank_version_fails_before_model(self):
        self.bank['rubric_version']='v1'
        self.tasks.submit({'kind':'analyze','requestId':str(uuid.uuid4()),'posting':{'text':'Fictional posting '*30}})
        self.tasks.process_one()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.tasks.postings(),[])

    def test_drafting_prompt_excludes_private_assessment(self):
        prompt=ai.prompt_for('cv',{'company':'Fictional employer','text':'Fictional posting',
                                  'fit':{'want':'Dream','actor':'PRIVATE_WANT_CANARY'}},self.bank)
        self.assertNotIn('PRIVATE_WANT_CANARY',prompt)

    def link_only(self,text=''):
        p={'kind':'analyze','requestId':str(uuid.uuid4()),'posting':{'company':'Example Company','role':'Example Role','link':'https://jobs.example.com/1','text':text},'images':[]}
        key=self.tasks.submit(p)
        self.tasks.process_one()
        return key

    def test_link_alone_is_read_and_used(self):
        self.link_text='Fictional responsibilities and requirements. '*20
        key=self.link_only()
        self.assertEqual(self.links,['https://jobs.example.com/1'])
        self.assertIn('Fictional responsibilities',self.calls[0][0])
        record=self.tasks.posting(key)
        self.assertEqual(record['source'],'link')
        self.assertIn('Fictional responsibilities',record['text'])

    def test_unreadable_link_alone_gives_a_clear_error(self):
        self.link_text=None
        self.link_only()
        task=self.tasks.list_tasks()[0]
        self.assertEqual(task['status'],'failed')
        self.assertIn('could not read that link',task['message'])
        self.assertEqual(self.calls,[])

    def test_pasted_text_stays_and_link_text_is_added(self):
        self.link_text='Fictional link body. '*30
        key=self.link_only('Fictional pasted requirement. '*10)
        text=self.tasks.posting(key)['text']
        self.assertTrue(text.startswith('Fictional pasted requirement.'))
        self.assertIn('Fictional link body.',text)

    def test_failed_link_with_enough_pasted_text_still_works(self):
        self.link_text=None
        key=self.link_only('Fictional pasted requirement. '*10)
        self.assertEqual(self.tasks.list_tasks()[0]['status'],'done')
        self.assertEqual(self.tasks.posting(key)['source'],'text')
        self.assertEqual(self.tasks.posting(key)['fit']['flags'],[])

    def run_matches(self,matches):
        self.result=dict(self.result,matches=matches)
        key=self.analyze()
        return self.tasks.posting(key)['fit']

    def test_evidence_text_that_is_not_an_id_does_not_fail_the_review(self):
        fit=self.run_matches([{'requirement':'A','importance':'must','evidence':'None','level':'partial'},
                              {'requirement':'B','importance':'nice','evidence':'','level':'gap'}])
        self.assertEqual([m['evidence'] for m in fit['matches']],['',''])
        self.assertEqual([m['level'] for m in fit['matches']],['gap','gap'])
        self.assertTrue(any('cannot use' in f for f in fit['flags']))

    def test_ineligible_ids_are_dropped_and_eligible_ones_kept(self):
        fit=self.run_matches([{'requirement':'A','importance':'must','evidence':'E-999, E-123','level':'strong'},
                              {'requirement':'B','importance':'must','evidence':'E-123','level':'strong'}])
        self.assertEqual(fit['matches'][0]['evidence'],'E-999')
        self.assertEqual(fit['matches'][0]['level'],'strong')
        self.assertEqual(fit['matches'][1]['evidence'],'')
        self.assertEqual(fit['matches'][1]['level'],'gap')
        self.assertIn('2 requirement(s)',fit['flags'][0])

    def test_longer_ids_are_not_cut_down_to_a_different_entry(self):
        fit=self.run_matches([{'requirement':'A','importance':'must','evidence':'E-9990','level':'strong'},
                              {'requirement':'B','importance':'must','evidence':'xE-999','level':'Strong'}])
        self.assertEqual([m['evidence'] for m in fit['matches']],['',''])
        self.assertEqual([m['level'] for m in fit['matches']],['gap','gap'])

    def test_level_is_normalised_and_no_evidence_always_means_gap(self):
        fit=self.run_matches([{'requirement':'A','importance':'must','evidence':'E-999','level':' Strong '},
                              {'requirement':'B','importance':'must','evidence':'','level':'match'}])
        self.assertEqual([m['level'] for m in fit['matches']],['strong','gap'])

    def test_clean_evidence_adds_no_flag(self):
        fit=self.run_matches([{'requirement':'A','importance':'must','evidence':'E-999','level':'strong'}])
        self.assertEqual(fit['flags'],[])

    def test_letter_requires_explicit_sentiment_choice(self):
        key=self.analyze()
        with self.assertRaises(ValueError):
            self.tasks.submit({'kind':'letter','postingId':key,'requestId':str(uuid.uuid4())})
        self.result={'cover_letter_markdown':'Fictional letter.','sources':['E-999'],'flags':[],
                     'claims':[{'text':'Fictional letter.','evidence':['E-999']}]}
        self.tasks.submit({'kind':'letter','postingId':key,'sentiment':'Calm confidence','requestId':str(uuid.uuid4())})
        self.tasks.process_one()
        record=self.tasks.posting(key)
        self.assertEqual(record['fit']['sentiment'],'Calm confidence')
        self.assertFalse(record['reviewed'])
        self.assertEqual(self.tasks.list_tasks()[0]['status'],'done')

    def test_unknown_sources_are_blocked(self):
        key=self.analyze()
        self.result={'cv_markdown':'An invented claim.','sources':['E-998'],'flags':[],
                     'claims':[{'text':'An invented claim.','evidence':['E-998']}]}
        self.tasks.submit({'kind':'cv','postingId':key,'requestId':str(uuid.uuid4())})
        self.tasks.process_one()
        self.assertEqual(next(t for t in self.tasks.list_tasks() if t['kind']=='cv')['status'],'failed')
        self.assertNotIn('cv_markdown',self.tasks.posting(key)['fit'])

    def test_missing_analysis_and_invalid_photo(self):
        with self.assertRaises(ValueError):
            self.tasks.submit({'kind':'cv','postingId':str(uuid.uuid4()),'requestId':str(uuid.uuid4())})
        with self.assertRaises(ValueError):
            self.tasks.submit({'kind':'analyze','requestId':str(uuid.uuid4()),'posting':{'text':'Fake posting '*30},'images':['bm90LWFuLWltYWdl']})
        self.assertEqual(self.calls,[])

    def test_restart_does_not_duplicate_running_task(self):
        key=self.tasks.submit({'kind':'analyze','requestId':str(uuid.uuid4()),'posting':{'text':'Fictional posting '*20},'images':[]})
        with self.tasks.connect() as db,db:
            db.execute("UPDATE tasks SET status='running' WHERE id=?",(key,))
        resumed=ai.Tasks(self.state,runner=self.runner,bank_loader=lambda:self.bank)
        resumed.process_one()
        self.assertEqual(resumed.list_tasks()[0]['status'],'interrupted')
        self.assertEqual(self.calls,[])


spec=importlib.util.spec_from_file_location('pc_files_fixture',HERE/'pc_files.py')
files=importlib.util.module_from_spec(spec)
spec.loader.exec_module(files)
conv=files.converter()

CLEAN_LETTER='''Dear Example Company team,

I would like to apply for the Example Role. Fictional example skill is what I bring.

Kind regards,
Alex Example'''
DIRTY_LETTER='''DRAFT
Draft: awaiting truth review
[HQ]
**Sentiment:** Calm confidence
Note: fictional reviewer note
Word count: 40

Dear Example Company team,

> I would like to apply for the **Example Role**. Fictional example skill is what I bring.
---
Kind regards,
Alex Example
(About 40 words)
Signed\x3a Codex'''
MARKERS=('Sentiment','Signed','DRAFT','Draft:','[HQ]','(About','Note:','Word count')
# Real content that only looks like a marker: it must survive every check and converter unchanged.
LOOKALIKES=['Signed 3 enterprise partners in a fictional pilot.','Sentiment analysis pipeline for fictional reviews.','Note-taking agent for fictional meetings.',
            'Note that I relocated to a fictional city.','Built a fictional [RAG] prototype with [TODO list] tracking.','Drafted a fictional plan, 2026 (About the team).',
            'Built a [RAG] evaluation harness for a fictional team.','Notes app for fictional field teams','(About the role) a fictional summary.','_Sentiment analysis_ in fictional reviews']


class CleanDocuments(unittest.TestCase):
    """Letters and CVs carry only the document."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hq-docs-test-')
        self.dir=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def read(self,path):
        import docx
        d=docx.Document(path)
        text='\n'.join(p.text for p in d.paragraphs)
        head='\n'.join(p.text for s in d.sections for part in (s.header,s.footer) for p in part.paragraphs)
        return d,text,head

    def test_letter_docx_is_plain_paragraphs_without_markers(self):
        record={'company':'Example Company','fit':{'cover_letter_markdown':DIRTY_LETTER}}
        path=files.export_draft(self.dir,str(uuid.uuid4()),'letter',record,copy_dir=self.dir/'no-copy'/'Job Search')
        self.assertNotIn('DRAFT',path.name)
        d,text,head=self.read(path)
        self.assertEqual({p.style.name for p in d.paragraphs},{'Normal'})
        self.assertFalse(any(p.style.name.startswith(('Heading','Title')) for p in d.paragraphs))
        for marker in MARKERS:self.assertNotIn(marker,text)
        self.assertNotIn('**',text);self.assertNotIn('---',text);self.assertNotIn('>',text)
        self.assertEqual(head.strip(),'')
        self.assertIn('Dear Example Company team,',text)
        self.assertIn('Kind regards,\nAlex Example',text)
        self.assertEqual((d.styles['Normal'].font.name,d.styles['Normal'].font.size.pt),('Calibri',11))
        self.assertEqual(d.core_properties.title,'')

    def test_cv_docx_keeps_name_header_but_no_markers(self):
        cv='# Alex Example\n[email] | [phone] | [LinkedIn URL]\n[DRAFT]\nNote: for the reviewer\n## Skills\n- Drafted fictional example plans\n- **Fictional** example skill'
        path=files.export_draft(self.dir,str(uuid.uuid4()),'cv',{'company':'Example','fit':{'cv_markdown':cv}},copy_dir=self.dir/'none'/'x')
        d,text,head=self.read(path)
        self.assertEqual(d.paragraphs[0].text,'Alex Example')
        self.assertIn('[LinkedIn URL]',text)
        self.assertIn('Drafted fictional example plans',text)
        self.assertNotIn('[DRAFT]',text);self.assertNotIn('Note:',text);self.assertEqual(head.strip(),'')

    def test_validator_rejects_each_marker_and_accepts_clean(self):
        self.assertIsNone(conv.draft_problem(CLEAN_LETTER,'letter'))
        self.assertIsNone(conv.draft_problem('# Alex Example\n[email] | [phone]\n## Skills\n- Drafted plans','cv'))
        bad={'# Cover letter':'letter','> quoted line':'letter','--- ':'letter','[HQ]':'letter','[DRAFT]':'cv','[/HQ]':'cv','[TODO] [PLACEHOLDER]':'letter',
             '[REVIEWED]':'cv','Sentiment: warm':'letter','Signed\x3a Codex':'cv','Note: check this':'letter','Notes: x':'cv','Word count: 210':'letter',
             '(About 210 words)':'letter','(210 words)':'cv','DRAFT':'cv','Draft: v2':'letter','**Sentiment:** warm':'letter'}
        for line in LOOKALIKES:
            for kind in ('cv','letter'):
                with self.subTest(lookalike=line,kind=kind):
                    self.assertIsNone(conv.draft_problem(CLEAN_LETTER+'\n\n'+line,kind))
        for line,kind in bad.items():
            with self.subTest(line=line):
                problem=conv.draft_problem(CLEAN_LETTER+'\n'+line,kind)
                self.assertTrue(problem and problem.endswith('Try again.'),problem)

    def test_report_blocks_escape_and_flatten(self):
        md='# Fictional report\nIntro with **bold** and [a link](https://example.com/x).\n<script>alert(1)</script>\n\n| Company | Score |\n|---|---|\n| Example Co | 4.1 |\n\n- one\n- two'
        blocks=conv.report_blocks(md)
        self.assertEqual(blocks[0],{'type':'h','level':1,'runs':[['Fictional report',False]]})
        flat=json.dumps(blocks)
        self.assertIn('<script>alert(1)</script>',json.loads(flat)[1]['runs'][-1][0])
        self.assertIn(['bold',True],blocks[1]['runs'])
        self.assertIn('a link (https://example.com/x)',flat)
        table=next(b for b in blocks if b['type']=='table')
        self.assertEqual([(c['label'],c['runs'][0][0]) for c in table['rows'][0]],[('Company','Example Co'),('Score','4.1')])
        import io,docx
        stream=io.BytesIO();conv.report_to_docx(md,stream)
        text='\n'.join(p.text for p in docx.Document(io.BytesIO(stream.getvalue())).paragraphs)
        self.assertIn('Company: Example Co\nScore: 4.1',text)
        self.assertNotIn('|',text)

    def test_lookalike_content_survives_both_converters(self):
        body='\n\n'.join(LOOKALIKES)
        for kind,field in (('letter','cover_letter_markdown'),('cv','cv_markdown')):
            path=files.export_draft(self.dir,str(uuid.uuid4()),kind,{'company':'Example','fit':{field:'Sentiment: warm\n\n'+body+'\n\n[DRAFT]'}},copy_dir=self.dir/'none'/'x')
            _,text,_=self.read(path)
            for line in LOOKALIKES:
                with self.subTest(kind=kind,line=line):self.assertIn(line,text)
            self.assertNotIn('Sentiment: warm',text);self.assertNotIn('[DRAFT]',text)

    def test_existing_files_report_marker_lines(self):
        import docx
        clean,noted=self.dir/(files.SETTINGS['document_prefix'] + 'CL_Example_Clean.docx'),self.dir/(files.SETTINGS['document_prefix'] + 'CL_Example_Noted.docx')
        d=docx.Document();d.add_paragraph('Dear team,');[d.add_paragraph(x) for x in LOOKALIKES];d.save(clean)
        d=docx.Document();d.add_paragraph('Dear team,');d.add_paragraph('Sentiment: warm');d.save(noted)
        before={p:p.read_bytes() for p in (clean,noted)}
        found={x['name']:x['clean'] for x in files.documents(self.dir)}
        self.assertEqual(found,{clean.name:True,noted.name:False})
        self.assertEqual(before,{p:p.read_bytes() for p in (clean,noted)},'Listing never changes a file')

    def test_report_parsing_is_bounded_and_links_are_text(self):
        import time
        for hostile in ('['*40000,'!['*20000,('['*1000+'\n')*40):
            start=time.perf_counter();conv.report_blocks(hostile);elapsed=time.perf_counter()-start
            self.assertLess(elapsed,1.0,f'{hostile[:2]!r} took {elapsed:.2f}s')
        self.assertTrue(all(len(r[0])<=2000 for b in conv.report_blocks('x'*50000) for r in b['runs']))
        flat=json.dumps(conv.report_blocks('See [link](javascript:alert(3)) and [site](https://example.com/a).'))
        self.assertIn('See link and site (https://example.com/a).',flat)
        self.assertNotIn('javascript',flat)

    def test_bulleted_and_emphasized_markers(self):
        for line in ('- Sentiment: confident','* Signed\x3a Codex','  - Note: check','_Signed\x3a Codex_','**Signed\x3a**','+ Draft: v2','• Word count: 210','> - **Notes:** x'):
            for kind in ('cv','letter'):
                with self.subTest(line=line,kind=kind):
                    self.assertTrue(conv.marker_line(line))
                    self.assertIsNotNone(conv.draft_problem(CLEAN_LETTER+'\n'+line,kind))
            self.assertNotIn(line.strip(),conv.clean_text('Keep this.\n'+line))

    def test_known_tag_inside_a_line(self):
        for line in ('[HQ] Dear Example team,','Dear team, [HQ]','Fictional [DRAFT] skill','Done [/HQ]'):
            with self.subTest(line=line):self.assertIsNotNone(conv.draft_problem(CLEAN_LETTER+'\n'+line,'letter'))
        self.assertEqual(conv.clean_text('[HQ] Dear Example team,\nDear team, [HQ]\nA [TODO] fictional  [RAG] item [Note]'),
                         'Dear Example team,\nDear team,\nA fictional [RAG] item [Note]')
        path=files.export_draft(self.dir,str(uuid.uuid4()),'letter',{'company':'Example','fit':{'cover_letter_markdown':'[HQ] Dear Example team,\n\nBuilt a [RAG] harness. [DRAFT]'}},copy_dir=self.dir/'none'/'x')
        _,text,_=self.read(path)
        self.assertEqual(text,'Dear Example team,\nBuilt a [RAG] harness.')

    def test_header_or_footer_text_is_not_clean(self):
        import docx
        for part in ('header','footer'):
            path=self.dir/(files.SETTINGS['document_prefix'] + f'CV_Example_{part}.docx')
            d=docx.Document();d.add_paragraph('Fictional body.');getattr(d.sections[0],part).paragraphs[0].text='Fictional '+part+' text';d.save(path)
        plain=self.dir/(files.SETTINGS['document_prefix'] + 'CV_Example_plain.docx')
        d=docx.Document();d.add_paragraph('Fictional body.');d.save(plain)
        found={x['name']:x['clean'] for x in files.documents(self.dir)}
        self.assertEqual(found,{(files.SETTINGS['document_prefix'] + 'CV_Example_header.docx'):False,(files.SETTINGS['document_prefix'] + 'CV_Example_footer.docx'):False,plain.name:True})

    def test_pairing_links_cv_and_letter(self):
        import docx
        for name in ((files.SETTINGS['document_prefix'] + 'CV_Example_Ai_Engineer.docx'),(files.SETTINGS['document_prefix'] + 'CL_Example_Ai_Engineer.docx'),(files.SETTINGS['document_prefix'] + 'CV_Other_Role.docx')):
            docx.Document().save(self.dir/name)
        docs=files.documents(self.dir)
        pairs={(d['kind'],d['pair']) for d in docs}
        self.assertEqual(pairs,{('cv','Example_Ai_Engineer'),('cl','Example_Ai_Engineer'),('cv','Other_Role')})
        self.assertEqual({d['kind'] for d in files.existing(self.dir)},{'cv'})
        self.assertEqual(len({d['id'] for d in docs}),3)


def _marker_letter_fails_and_clean_letter_saves(self):
        key=self.analyze()
        self.result={'cover_letter_markdown':'Sentiment: warm\n\n'+CLEAN_LETTER,'sources':['E-999'],'flags':[],
                     'claims':[{'text':'Fictional example skill','evidence':['E-999']}]}
        self.tasks.submit({'kind':'letter','postingId':key,'sentiment':'Warm','requestId':str(uuid.uuid4())})
        self.tasks.process_one()
        # Select by kind: two tasks can share a timestamp, so list order alone is not reliable.
        task=next(t for t in self.tasks.list_tasks() if t['kind']=='letter')
        self.assertEqual(task['status'],'failed')
        self.assertIn('marker line',task['message'])
        self.assertNotIn('cover_letter_markdown',self.tasks.posting(key)['fit'])
        self.result=dict(self.result,cover_letter_markdown=CLEAN_LETTER)
        self.tasks.submit({'kind':'letter','postingId':key,'sentiment':'Warm','requestId':str(uuid.uuid4())})
        self.tasks.process_one()
        self.assertEqual(sorted(t['status'] for t in self.tasks.list_tasks() if t['kind']=='letter'),['done','failed'])
        self.assertIn('no bracket tags such as [HQ]',self.calls[-1][0])


CareerTasks.test_marker_letter_fails_and_clean_letter_saves=_marker_letter_fails_and_clean_letter_saves


if __name__=='__main__':unittest.main(verbosity=2)
