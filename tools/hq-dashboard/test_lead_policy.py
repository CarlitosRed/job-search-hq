"""Offline integration and exact browser/ranker parity; fictional inputs only.
 Maintainer executes, builder does not.
"""
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lead_policy as policy
from lead_policy_fixtures import fictional_result, cleared_context

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
WANT = {'want':'Dream','actor':'Fictional the owner','at':NOW.isoformat()}


class ProductionBoundary(unittest.TestCase):
    #
    def test_importance_schema_and_legacy_packet(self):
        import ai_tasks
        spec = ai_tasks.schema('analyze')['properties']['matches']['items']['properties']['importance']
        self.assertEqual(spec, {'type':'string', 'enum':['must','nice']})
        legacy = fictional_result('SSPS')
        canonical = fictional_result('SSPS')
        for m, value in zip(legacy['matches'], ('core', 'requested', 'preferred', 'core; learning expected')):
            m['importance'] = value
        canonical['matches'][2]['importance'] = 'nice'
        self.assertEqual(self.rank(legacy), self.rank(canonical))
        self.assertEqual([m['importance'] for m in legacy['matches']], ['must','must','nice','must'])

    def test_invalid_match_fields_have_browser_python_error_parity(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Install Node for required validation parity')
        cases = []
        for field, values in [('importance', ['required-ish', '', None, 7, {}, '; core', 'unknown; must']),
                              ('clause', ['', '   ', None, 7]), ('rationale', ['', '   ', None, 7])]:
            for value in values:
                out = fictional_result()
                out['matches'][0][field] = value
                with self.assertRaisesRegex(ValueError, 'invalid ' + field) as error:
                    self.rank(out)
                cases.append(dict(out=out, error=str(error.exception)))
        source = (HERE/'hosted/lead-policy.js').read_text(encoding='utf-8')
        script = source + '''
const p=JSON.parse(require('fs').readFileSync(0,'utf8'));
console.log(JSON.stringify(p.cases.map(c=>{try{
 LeadPolicy.rank(c.out,'https://example.com/fictional','Fictional employer',p.now,{}, {},p.schema);
 return null;
}catch(e){return e.message}})));
'''
        done = subprocess.run([node, '-e', script], text=True, capture_output=True, check=True,
                              input=json.dumps(dict(cases=cases, now=NOW.isoformat(), schema=policy.extraction_schema())))
        self.assertEqual(json.loads(done.stdout), [c['error'] for c in cases])

    #
    @unittest.skip("Private seed/profile builder is not shipped in the fictional export")
    def test_generated_prompts_and_hosted_bank_exclude_private_policy(self):
        pass

    def rank(self, out, context=None, want=None):
        return policy.rank_result(out, 'https://example.com/fictional', 'Fictional employer', NOW, context, want)

    def test_cleared_dream_safe_and_commercial_analogues(self):
        # Fictional analogues cover dream, safe and commercial outcomes.
        dream = fictional_result('SSPPPT')
        dream['analysis'].update(work='5',growth='5')
        self.assertEqual(self.rank(dream,cleared_context(),WANT)['lane'],'Ready')
        self.assertEqual(self.rank(dream,cleared_context(),WANT)['tags'],['Dream'])
        self.assertEqual(self.rank(fictional_result(),cleared_context())['lane'],'Ready')
        self.assertEqual(self.rank(fictional_result('SSSPTT'),cleared_context())['lane'],'Stretch')
        # Independent paperwork cannot be invented by a model output.
        self.assertEqual(self.rank(fictional_result())['lane'],'Stretch')
        self.assertEqual(self.rank(dream,dict(cleared_context(),applied=True),WANT)['strip'],'Follow-up')

    def test_version_and_schema_fail_closed(self):
        out=fictional_result()
        out['rubric_version']='v1'
        with self.assertRaisesRegex(ValueError,'version mismatch'):self.rank(out)
        out=fictional_result();out['analysis']['source_open']='true'
        with self.assertRaises(ValueError):self.rank(out)
        out=fictional_result();out['analysis']['want']='Dream'
        with self.assertRaises(ValueError):self.rank(out)
        out=fictional_result();out['matches'][0]['importance']='required-ish'
        with self.assertRaises(ValueError):self.rank(out)

    def test_blank_want_and_english_soft_only(self):
        out=fictional_result('SSSSSS')
        out['analysis'].update(english_clause='C2 English required',english_force='required')
        result=self.rank(out,cleared_context())
        self.assertEqual((result['A'],result['V'],result['want'],result['lane']),('19/4','5/2','Not set','Ready'))
        out['analysis']['hard_blockers']=[dict(kind='other_language',clause='C2 English',conflict='B2 only',confirmed=True)]
        with self.assertRaisesRegex(ValueError,'soft flag'):self.rank(out)

    def test_graded_years_and_unknown_scope(self):
        for required,domain,title,expected in [('4','analyst',True,'Ready'),('5','analyst',False,'Stretch'),
                                               ('5','analyst',True,'Monitor'),('7','analyst',False,'Monitor'),
                                               ('2','special-tool',False,'Verify')]:
            out=fictional_result('SSSSSS')
            out['analysis'].update(work='5',growth='5',title_seniority=title,
                years_clauses=[dict(clause='Fictional years clause',required_years=required,
                    years_domain=domain,relevant_years='2' if domain == 'analyst' else None,relevant_years_source='Fictional documented analyst tenure' if domain == 'analyst' else '',
                    equivalent_experience_accepted=False,legal_minimum=False,legal_conflict_confirmed=False)])
            self.assertEqual(self.rank(out,cleared_context())['lane'],expected)

    def test_verify_due_survives_reload(self):
        out=fictional_result();out['analysis']['eligibility_clear']=False
        first=self.rank(out)
        context={'verify_state':first['verify_state']}
        later=policy.rank_result(out,'https://example.com/fictional','Fictional employer',
                                datetime(2026,11,9,12,tzinfo=timezone.utc),context)
        self.assertEqual(first['verify_state'],later['verify_state'])

    def test_browser_exact_values_and_lanes_match_python(self):
        node=shutil.which('node')
        self.assertIsNotNone(node,'Install Node for the required browser/server parity check')
        cases=[]
        #
        for value in ('core', 'required', 'requested', 'must', 'mandatory',
                      'preferred', 'nice', 'bonus', 'plus', 'advantageous',
                      'core; learning expected', ' PREFERRED ; optional detail'):
            out = fictional_result()
            out['matches'][0]['importance'] = value
            cases.append(dict(out=out, context=cleared_context(), want={}))
        out = fictional_result('SSPS')
        for m, value in zip(out['matches'], ('core', 'requested', 'preferred', 'core; learning expected')):
            m['importance'] = value
        cases.append(dict(out=out, context=cleared_context(), want={}))
        for ratings in ('SSSSSS','SSSSPT','SSSPTT','SSPPPT','TTTTTT'):
            for context in ({},cleared_context()):
                for want in ({},WANT):
                    cases.append(dict(out=fictional_result(ratings),context=context,want=want))
        for required,domain,title,equiv,legal,confirmed in [
                ('4','analyst',True,False,False,False),('5','analyst',False,False,False,False),
                ('5','analyst',True,False,False,False),('7','analyst',False,True,False,False),
                ('3','unknown',False,False,False,False),('4','analyst',False,False,True,False),
                ('4','analyst',False,False,True,True)]:
            out=fictional_result('SSSSSS');out['analysis'].update(work='5',growth='5',title_seniority=title,
                years_clauses=[dict(clause='Fictional years clause',required_years=required,years_domain=domain,
                    relevant_years='2' if confirmed else None,relevant_years_source='Fictional exact tenure' if confirmed else '',
                    equivalent_experience_accepted=equiv,legal_minimum=legal,legal_conflict_confirmed=confirmed)])
            cases.append(dict(out=out,context=cleared_context(),want=WANT))
        for changes in [dict(english_clause='C2 English required',english_force='required'),dict(work=None),
                        dict(source_checked_at='2026-11-05T12:00:00Z'),dict(eligibility_clear=False),
                        dict(deadline='2026-11-08T12:00:00Z',deadline_verified=True),
                        dict(hard_blockers=[dict(kind='geography',clause='Fictional country only',conflict='Fictional exclusion',confirmed=True)])]:
            out=fictional_result();out['analysis'].update(changes)
            cases.append(dict(out=out,context=cleared_context(),want={}))
        source=(HERE/'hosted/lead-policy.js').read_text(encoding='utf-8')
        script=source+'''
const fs=require('fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(p.cases.map(c=>LeadPolicy.rank(c.out,'https://example.com/fictional','Fictional employer',p.now,c.context,c.want,p.schema))));
'''
        done=subprocess.run([node,'-e',script],input=json.dumps(dict(cases=cases,now=NOW.isoformat(),schema=policy.extraction_schema())),
                            text=True,capture_output=True,check=True)
        actual=json.loads(done.stdout)
        for case,js in zip(cases,actual):
            expected=self.rank(case['out'],case['context'],case['want'])
            for field in ('rubric_version','A','D','V','U','Q','want','lane','tags','years_class','strip'):
                self.assertEqual(js[field],expected[field],(case,field))
            if expected['years_class'] == 'HARD':
                self.assertEqual(case['out']['analysis']['hard_blockers'], [])
                self.assertEqual(js['lane'], 'Archive')
                self.assertEqual(js['years_reason'], expected['years_reason'])
                restriction = 'Archive: Confirmed restriction: ' + expected['years_reason']
                self.assertIn(restriction, expected['why'])
                self.assertIn(restriction, js['why'])


if __name__=='__main__':unittest.main()
