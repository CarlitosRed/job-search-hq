/* Browser boundary for lead_ranker.py.
   Exact rational scores; server/browser parity fixtures are maintained in test_lead_policy.py.
   Private context/Want are joined after model processing, never supplied in a prompt. */
const LEAD_RUBRIC_VERSION='v3.2';
const LeadPolicy=(()=>{
 //
 function normalizeImportance(value){if(typeof value==='string'){const token=value.split(';',1)[0].trim().toLowerCase();
  if(['core','required','requested','must','mandatory'].includes(token))return 'must';
  if(['preferred','nice','bonus','plus','advantageous'].includes(token))return 'nice'}
  throw Error('Each requirement needs its original clause and scope rationale: invalid importance')}
 const bad=m=>{throw Error(m)},gcd=(a,b)=>b?gcd(b,a%b):a<0n?-a:a;
 function R(x){if(x===null||x===undefined)return null;if(Array.isArray(x))return x;
  if(typeof x==='boolean'||typeof x==='number')bad('Use exact decimal text');
  const s=String(x);let n,d;if(/^-?\d+\/\d+$/.test(s)){[n,d]=s.split('/').map(BigInt)}else if(/^-?\d+(?:\.\d+)?$/.test(s)){const [a,b='']=s.split('.');n=BigInt(a+b);d=10n**BigInt(b.length)}else bad('Invalid exact number');
  if(!d)bad('Invalid denominator');const g=gcd(n,d);return[n/g,d/g]}
 const add=(a,b)=>R([a[0]*b[1]+b[0]*a[1],a[1]*b[1]]).map((v,i,ar)=>v/gcd(ar[0],ar[1]));
 const mul=(a,b)=>{const n=a[0]*b[0],d=a[1]*b[1],g=gcd(n,d);return[n/g,d/g]},neg=a=>[-a[0],a[1]],sub=(a,b)=>add(a,neg(b)),ge=(a,b)=>a[0]*b[1]>=b[0]*a[1],gt=(a,b)=>a[0]*b[1]>b[0]*a[1];
 const t=x=>R(String(x)),fmt=x=>x===null?null:x[1]===1n?String(x[0]):`${x[0]}/${x[1]}`;
 const date=x=>{if(typeof x!=='string'||!/(?:Z|[+-]\d\d:\d\d)$/.test(x)||!Number.isFinite(Date.parse(x)))bad('Timezone-aware date required');return Date.parse(x)};
 const bool=x=>{if(typeof x!=='boolean')bad('Real boolean required');return x};
 function validate(v,s){if(s.type==='object'){if(!v||typeof v!=='object'||Array.isArray(v)||Object.keys(v).sort().join('|')!==s.required.slice().sort().join('|'))bad('Missing/unexpected extraction fields');for(const[k,p]of Object.entries(s.properties))validate(v[k],p)}
  else if(s.type==='array'){if(!Array.isArray(v))bad('Expected array');for(const x of v)validate(x,s.items)}
  else{const types=Array.isArray(s.type)?s.type:[s.type];if(!types.includes(v===null?'null':typeof v))bad('Invalid extraction type');if(s.enum&&!s.enum.includes(v))bad('Invalid extraction enum')}}
 function rank(out,key,employer,now=new Date().toISOString(),context={},want={},schema){
  if(out.rubric_version!==LEAD_RUBRIC_VERSION)bad('Rubric version mismatch; nothing saved');
  if(!key||!employer)bad('Canonical identity required');if(schema)validate(out.analysis,schema);
  const a=out.analysis,n=date(now),matches=out.matches;if(!Array.isArray(matches))bad('Requirement matches required');
  const blockers=a.hard_blockers.map(b=>{if(!b.confirmed||!b.clause.trim()||!b.conflict.trim())bad('Hard blocker needs confirmed conflict');if(b.kind==='other_language'&&/\bEnglish\b|\bC[12]\b/i.test(b.clause))bad('English is a soft flag only');return b.clause+'; '+b.conflict});
  const flags=['title_seniority','source_open','eligibility_clear','requirements_complete','deadline_verified','imminent_filling_verified','urgency_conflict'];flags.forEach(k=>bool(a[k]));
  const allowed=['owner_hold','owner_check_complete','packet_assessed','base_cv_version','cv_keyword_passed','preparation_clear','preparation_holds','adjudication_pending','plausible_half_coverage','english_adjudication_pending','applied','applied_at','work_model','verify_state'];
  if(Object.keys(context).some(k=>!allowed.includes(k)))bad('Unexpected private context');
  for(const k of allowed.filter(k=>!['base_cv_version','preparation_holds','applied_at','work_model','verify_state'].includes(k)))if(k in context)bool(context[k]);
  for(const m of matches){if(!['strong','partial','gap'].includes(m.level))bad('Invalid requirement evidence level');
   m.importance=normalizeImportance(m.importance);
   for(const field of ['clause','rationale'])if(typeof m[field]!=='string'||!m[field].trim())bad('Each requirement needs its original clause and scope rationale: invalid '+field)}
  const core=matches.filter(m=>m.importance!=='nice');let total=t(0);
  for(const m of matches){
   if(m.importance!=='nice')total=add(total,t(!m.evidence||m.level==='gap'?0:m.level==='strong'?1:'.5'.replace(/^\./,'0.')))}
  const C=core.length?mul(total,R(`1/${core.length}`)):null;
  // The page supplies the same tenure settings as Python.
  const floors=(typeof HQ_SETTINGS==="undefined"?{}:HQ_SETTINGS.documented_relevant_years||{}),priority={NONE:0,MET:0,TEACHABLE:1,HEAVY:2,UNKNOWN:3,'SENIORITY MISMATCH':4,HARD:5};
  let yr={kind:'NONE',gap:null,penalty:t(0),reason:''};
  for(const c of a.years_clauses){for(const k of ['equivalent_experience_accepted','legal_minimum','legal_conflict_confirmed'])bool(c[k]);
   let q={kind:'NONE',gap:null,penalty:t(0),reason:''};const req=R(c.required_years),rel=c.relevant_years===null?(c.years_domain in floors?t(floors[c.years_domain]):null):R(c.relevant_years);
   if(req&&(!ge(req,t(0))))bad('Negative required years');if(rel&&!ge(rel,t(0)))bad('Negative relevant years');
   if(c.relevant_years!==null&&!c.relevant_years_source.trim())bad('Years source required');
   if(!req){if(c.legal_minimum)q.kind='UNKNOWN'}else if(!rel){q.kind='UNKNOWN'}else{q.gap=sub(req,rel);
    q.reason=`asks for ${fmt(req)} years; you have ${c.relevant_years===null&&typeof HQ_SETTINGS!=="undefined"?(HQ_SETTINGS.documented_years_labels[c.years_domain]||"about "+fmt(rel)):"about "+fmt(rel)} in the relevant work`;
    if(!gt(q.gap,t(0))){q.kind=ge(q.gap,t(0))?'TEACHABLE':'MET';q.reason=''}
    else if(c.legal_minimum){q.kind=c.relevant_years!==null&&c.legal_conflict_confirmed?'HARD':'UNKNOWN';if(q.kind==='HARD')q.reason+='; confirmed legal or contractual minimum'}
    else if((ge(q.gap,t(5))||gt(q.gap,t(2))&&a.title_seniority)&&!c.equivalent_experience_accepted)q.kind='SENIORITY MISMATCH';
    else if(gt(q.gap,t(2))){q.kind='HEAVY';q.penalty=t('0.75')}else{q.kind='TEACHABLE';q.penalty=t('0.25')}}
   if(priority[q.kind]>priority[yr.kind]||priority[q.kind]===priority[yr.kind]&&(gt(q.penalty,yr.penalty)||q.gap&&(yr.gap===null||gt(q.gap,yr.gap))))yr=q;
  }
  const english=a.english_force==='required'&&/\b(?:C[12]|near[ -]native|native|fluent|fluency)\b|excellent\s+(?:written|verbal)\b/i.test(a.english_clause);
  let A=C===null?null:sub(add(t(2),mul(t(3),C)),add(english?t('0.25'):t(0),yr.penalty));if(A&&!ge(A,t(0)))A=t(0);
  const work=R(a.work),growth=R(a.growth);for(const v of [work,growth])if(v&&(!ge(v,t(0))||gt(v,t(5))))bad('Direction outside 0-5');
  const D=work&&growth?add(mul(t('0.7'),work),mul(t('0.3'),growth)):null;
  if(Object.keys(want).some(k=>!['want','actor','at'].includes(k)))bad('Invalid private Want');
  const w=want.want||'',wv={'':t('2.5'),Neutral:t('2.5'),Dream:t(5),Yes:t(4),No:t(0)};
  if(!(w in wv)||w&&(!want.actor||!want.actor.trim()||!want.at))bad('Explicit Want needs actor and time');if(w)date(want.at);const V=wv[w];
  const dl=a.deadline===null?null:date(a.deadline);let U=t('2.5');
  if(a.deadline_verified&&dl!==null){const delta=dl-n;if(delta>=0&&delta<=48*3600000)U=t(5);else if(delta>48*3600000&&delta<=7*86400000)U=t(4);else if(delta>7*86400000&&delta<=14*86400000)U=t(3)}
  if(a.imminent_filling_verified&&!ge(U,t(3)))U=t(3);
  const Q=A&&D?add(mul(t('0.9'),add(add(mul(t('0.6'),A),mul(t('0.3'),D)),mul(t('0.1'),U))),mul(t('0.1'),V)):null;
  const checked=a.source_checked_at===null?null:date(a.source_checked_at);if(checked!==null&&checked-n>300000)bad('Source check is in the future');
  const fresh=checked!==null&&n-checked<=86400000,holds=[...(context.preparation_holds||[])];
  if(!context.packet_assessed)holds.push('packet unassessed');if(!context.base_cv_version||!context.cv_keyword_passed)holds.push('versioned base CV keyword check pending');
  if(!context.preparation_clear)holds.push('preparation clearance pending');if(context.adjudication_pending)holds.push('requirement adjudication pending');if(context.english_adjudication_pending)holds.push('English clause adjudication pending');
  const half=C&&ge(C,t('0.5')),supported=half&&A&&ge(A,t('3.25'))||context.adjudication_pending&&context.plausible_half_coverage;
  let lane,reason;
  // Preserve the confirmed legal-years conflict.
  if(blockers.length||yr.kind==='HARD'){lane='Archive';reason='Confirmed restriction: '+[...blockers,...(yr.kind==='HARD'?[yr.reason]:[])].join('; ')}
  else if(context.owner_hold||w==='No'){lane='Monitor';reason=(typeof HQ_SETTINGS==="undefined"?"Owner recommendation hold":HQ_SETTINGS.recommendation_hold_reason)}
  else if(yr.kind==='SENIORITY MISMATCH'){lane='Monitor';reason='Seniority mismatch: '+yr.reason}
  else if(!a.source_open||!fresh||!a.eligibility_clear||!a.requirements_complete||C===null||a.urgency_conflict||yr.kind==='UNKNOWN'||a.deadline_verified&&dl!==null&&dl<n){lane='Verify';reason='Resolve source freshness, availability, eligibility or requirements'}
  else if(yr.kind==='HEAVY'){const high=D&&ge(D,t('3.5'));lane=w==='Dream'&&half||high&&half&&ge(A,t('3.25'))||(w==='Dream'||high)&&context.adjudication_pending&&context.plausible_half_coverage?'Stretch':'Monitor';reason='Heavy years gap: '+yr.reason}
  else if(!context.owner_check_complete){lane=supported||!holds.length&&w==='Dream'&&half?'Stretch':'Monitor';reason='Owner-hold check pending'}
  else if(!holds.length&&half&&(ge(A,t(4))||w==='Dream')){lane='Ready';reason='Shared checks passed; honest application can go now; tailoring optional'}
  else if(supported){lane='Stretch';reason='Prepare/review: '+(holds.join('; ')||'teachable coverage gaps')}
  else{lane='Monitor';reason='Low supported coverage; reassess in weekly review'}
  let state=null;if(lane==='Verify'){state=context.verify_state||{due_at:new Date(n+86400000).toISOString(),previous_due_at:null,renewal_reason:''};date(state.due_at);if(state.previous_due_at&&(date(state.due_at)<=date(state.previous_due_at)||!state.renewal_reason.trim()))bad('Invalid Verify renewal')}
  const why=`Fits: ${matches.filter(m=>m.level!=='gap'&&m.evidence).map(m=>m.requirement).join(', ')||'coverage not established'}. Teachable gaps: ${matches.filter(m=>m.level!=='strong'||!m.evidence).map(m=>m.unsupported_scope||m.requirement).join(', ')||'none identified'}. ${lane}: ${reason}. Work model: ${context.work_model||'Unknown'}. ${yr.reason?'Years: '+yr.reason+'.':''}${D===null?' Direction Unknown; review duties and growth before numeric ranking.':''}`.replace(/\bE-\d+\b/g,'private evidence');
  return{rubric_version:LEAD_RUBRIC_VERSION,A:fmt(A),D:fmt(D),V:fmt(V),U:fmt(U),Q:fmt(Q),want:w||'Not set',lane,
   tags:lane==='Ready'?[...(w==='Dream'?['Dream']:[]),...(ge(A,t(4))?['Safe bet']:[])]:[],why,
   next_action:lane==='Verify'?(yr.kind==='UNKNOWN'?yr.reason||'Confirm documented years in the required work':'Check the exact source, eligibility and requirement clauses'):'',
   strip:context.applied?'Follow-up':'',label:context.applied?'Applied '+(context.applied_at||'date Unknown'):'',years_class:yr.kind,years_reason:yr.reason,verify_state:state};
 }
 return{rank,validate};
})();
const policyNumber=v=>{if(v===null||v===undefined)return 'Unknown';const[ns,ds='1']=String(v).split('/');let n=BigInt(ns),d=BigInt(ds),sign=n<0n?'-':'';if(n<0n)n=-n;n*=100n;let cents=n/d;if(2n*(n%d)>=d)cents++;return`${sign}${cents/100n}.${String(cents%100n).padStart(2,'0')}`};
function humanPolicyNotes(f){
 const s=String(f.summary||'Review the posting before deciding.');
 if(/\bE-\d+\b|[A-Za-z]:[\\/]|(?:work|evidence|audits|tools)[\\/]|\b(?:rubric|score|threshold|weighted)\b/i.test(s))throw Error('Put evidence references and scoring details in private agent notes.');
 return 'Lane: '+String(f.lane||'Verify')+'\nWhy it fits: '+s+'\nNext step: Review the original posting and remaining application checks.';
}
