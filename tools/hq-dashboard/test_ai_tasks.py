"""Offline career task tests with fictional evidence."""
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


class CareerTasks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hq-ai-test-')
        self.state=Path(self.temp.name)
        self.calls=[]
        self.bank={'rules':'Fictional evidence only.','entries':{'E-999':{'level':'stated','text':'Fictional example skill.'}}}
        self.result={'is_job_posting':True,'readable':True,'company':'Example Company','role':'Example Role','link':'https://example.com',
                     'posting_text':'','score':3.5,'verdict':'CONSIDER','summary':'Fictional match.','location_ok':'unclear',
                     'matches':[{'requirement':'Example skill','evidence':'E-999','importance':'must','level':'partial'}],'gaps':[],'flags':[]}
        def fake(prompt,kind,folder,images):
            self.calls.append((prompt,kind,images))
            return self.result
        self.runner=fake
        self.tasks=ai.Tasks(self.state,runner=fake,bank_loader=lambda:self.bank)

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
        self.result={'cv_markdown':'An invented claim.','sources':['E-013'],'flags':[],
                     'claims':[{'text':'An invented claim.','evidence':['E-013']}]}
        self.tasks.submit({'kind':'cv','postingId':key,'requestId':str(uuid.uuid4())})
        self.tasks.process_one()
        self.assertEqual(next(t for t in self.tasks.list_tasks() if t['kind']=='cv')['status'],'failed')
        self.assertNotIn('cv_markdown',self.tasks.posting(key)['fit'])

    def test_ineligible_claim_evidence_is_rejected(self):
        key=self.analyze()
        for kind,field in (('cv','cv_markdown'),('letter','cover_letter_markdown')):
            with self.subTest(kind=kind):
                self.result={field:'Fictional example skill.','sources':['E-999'],'flags':[],
                             'claims':[{'text':'Fictional example skill.','evidence':['E-998']}]}
                task_id=self.tasks.submit({'kind':kind,'postingId':key,'withoutSentiment':True,'requestId':str(uuid.uuid4())})
                self.tasks.process_one()
                task=next(t for t in self.tasks.list_tasks() if t['id']==task_id)
                self.assertEqual(task['status'],'failed')
                self.assertEqual(task['message'],'The draft cited evidence that is not eligible.')
                self.assertNotIn(field,self.tasks.posting(key)['fit'])

    def test_listed_claim_absent_from_document_is_rejected(self):
        key=self.analyze()
        for kind,field in (('cv','cv_markdown'),('letter','cover_letter_markdown')):
            with self.subTest(kind=kind):
                self.result={field:'Fictional example skill.','sources':['E-999'],'flags':[],
                             'claims':[{'text':'This claim is absent.','evidence':['E-999']}]}
                task_id=self.tasks.submit({'kind':kind,'postingId':key,'withoutSentiment':True,'requestId':str(uuid.uuid4())})
                self.tasks.process_one()
                task=next(t for t in self.tasks.list_tasks() if t['id']==task_id)
                self.assertEqual(task['status'],'failed')
                self.assertEqual(task['message'],'The evidence map does not match the document text.')
                self.assertNotIn(field,self.tasks.posting(key)['fit'])

    def test_documented_limitation_unlisted_factual_prose_is_accepted(self):
        # Claim coverage is not validated; a human must review the whole draft.
        key=self.analyze()
        content='Fictional example skill.\nI managed 500 fictional employees.'
        claims=[{'text':'Fictional example skill.','evidence':['E-999']}]
        for kind,field in (('cv','cv_markdown'),('letter','cover_letter_markdown')):
            with self.subTest(kind=kind):
                self.result={field:content,'sources':['E-999'],'flags':[],'claims':claims}
                task_id=self.tasks.submit({'kind':kind,'postingId':key,'withoutSentiment':True,'requestId':str(uuid.uuid4())})
                self.tasks.process_one()
                task=next(t for t in self.tasks.list_tasks() if t['id']==task_id)
                self.assertEqual(task['status'],'done')
                record=self.tasks.posting(key)
                self.assertEqual(record['fit'][field],content)
                self.assertEqual(record['fit'][kind+'_claims'],claims)
                self.assertFalse(record['reviewed'])

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
Signed : Codex'''
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
        path=files.export_draft(self.dir,str(uuid.uuid4()),'letter',record,copy_dir=self.dir/'no-copy_dir'/'Job Search')
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
             '[REVIEWED]':'cv','Sentiment: warm':'letter','Signed : Codex':'cv','Note: check this':'letter','Notes: x':'cv','Word count: 210':'letter',
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
        clean,noted=self.dir/'CL_Example_Clean.docx',self.dir/'CL_Example_Noted.docx'
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
        for line in ('- Sentiment: confident','* Signed : Codex','  - Note: check','_Signed : Codex_','**Signed :**','+ Draft: v2','• Word count: 210','> - **Notes:** x'):
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
            path=self.dir/f'CV_Example_{part}.docx'
            d=docx.Document();d.add_paragraph('Fictional body.');getattr(d.sections[0],part).paragraphs[0].text='Fictional '+part+' text';d.save(path)
        plain=self.dir/'CV_Example_plain.docx'
        d=docx.Document();d.add_paragraph('Fictional body.');d.save(plain)
        found={x['name']:x['clean'] for x in files.documents(self.dir)}
        self.assertEqual(found,{'CV_Example_header.docx':False,'CV_Example_footer.docx':False,plain.name:True})

    def test_pairing_links_cv_and_letter(self):
        import docx
        for name in ('CV_Example_Ai_Engineer.docx','CL_Example_Ai_Engineer.docx','CV_Other_Role.docx'):
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
