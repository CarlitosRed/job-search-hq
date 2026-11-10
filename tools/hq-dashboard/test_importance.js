/* Offline hosted importance regression. Fictional inputs only.
  
   Run: node tools/hq-dashboard/test_importance.js */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const policy=vm.runInNewContext(fs.readFileSync(path.join(__dirname,'hosted/lead-policy.js'),'utf8')+'\nLeadPolicy;');
const now='2026-11-07T12:00:00+00:00';
function packet(values){return{rubric_version:'v3.2',analysis:{
 work:'2',growth:'2',direction_reason:'Fictional duties',english_clause:'',english_force:'none',title_seniority:false,
 years_clauses:[],hard_blockers:[],source_open:true,source_checked_at:now,eligibility_clear:true,
 requirements_complete:true,deadline:null,deadline_verified:false,imminent_filling_verified:false,urgency_conflict:false},
 matches:values.map((importance,i)=>({requirement:`Fictional task ${i}`,importance,evidence:i===2?'':'E-999',
 level:i===2?'gap':'strong',clause:`Fictional original clause ${i}`,rationale:'Fictional supported scope',unsupported_scope:''}))}}
const rank=p=>policy.rank(p,'https://example.com/fictional','Fictional employer',now);
const fields=['A','D','V','U','Q','lane','strip'];
const scores=p=>{const r=rank(p);return fields.map(k=>r[k])};
const legacy=packet(['core','requested','preferred','core; learning expected']);
assert.deepEqual(scores(legacy),scores(packet(['must','must','nice','must'])));
assert.deepEqual(legacy.matches.map(m=>m.importance),['must','must','nice','must']);
for(const [canonical,aliases] of Object.entries({must:['core','required','requested','must','mandatory'],nice:['preferred','nice','bonus','plus','advantageous']})){
 for(const alias of aliases)for(const value of [alias,` ${alias.toUpperCase()} ; learning expected`]){
  assert.deepEqual(scores(packet([value,'must'])),scores(packet([canonical,'must'])));
 }
}
for(const [field,values] of Object.entries({importance:['required-ish','',null,7,{},'; core','unknown; must'],
 clause:['','   ',null,7],rationale:['','   ',null,7]}))for(const value of values){
 const p=packet(['core']);p.matches[0][field]=value;
 assert.throws(()=>rank(p),{message:'Each requirement needs its original clause and scope rationale: invalid '+field});
}
// Exercise the hosted prompt builder without loading page event handlers or making model calls.
const hosted=fs.readFileSync(path.join(__dirname,'hosted/tools.js'),'utf8');
const builders=hosted.slice(hosted.indexOf('const RULES='),hosted.indexOf('function cvPrompt('));
const prompt=vm.runInNewContext(builders+'\nanalyzePrompt({bank:"Fictional evidence",scoring_prompt:"",analysis_schema:{}},{text:"Fictional posting"},0);');
assert.match(prompt,/importance is exactly "must"/);
assert.match(prompt,/"nice" for preferred requirements/);
assert.match(prompt,/free-text nuance in rationale/);
console.log('PASS: mixed legacy packet, 20 alias variants, 15 invalid fields, canonical scores/lanes, hosted prompt');
