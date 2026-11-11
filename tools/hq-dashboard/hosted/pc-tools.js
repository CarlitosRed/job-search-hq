/* PC career tasks. Server owns prompts and records. */
let PC_LETTER='',CLS=[],REPORTS=[];
async function pcState(){
 const data=await pcFetch('/api/ai/state');
 // Refuse a stale server/page pair.
 if(data.rubric_version!==RUBRIC_VERSION)throw Error('Rubric version mismatch: restart HQ with the matching page and worker.');
 gotPostings(data.postings||[]);
 if(CUR){const fresh=POSTINGS.find(p=>p._id===CUR._id);if(fresh){CUR=fresh;renderResult(CUR)}}return data;
}
async function pcInit(){
 CAP.sample={};CAP.downloads={};
 CAP.db={doc:path=>({update:async patch=>{
  if(!path.startsWith('postings/')||Object.keys(patch).some(k=>k!=='jobId'))throw Error('This change requires a separate reviewer.');
  return pcFetch('/api/ai/link',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:path.slice(9),jobId:patch.jobId})});
 }})};
 PH={maxCount:5,maxInputBytes:8*1024*1024,mediaTypes:['image/jpeg','image/png']};
 $('#r-file').accept=PH.mediaTypes.join(',');$('#r-photos').hidden=false;renderThumbs();
 $('#r-go').disabled=false;$('#s-go').disabled=false;
 /* A server started before the Documents routes existed still lists CVs (the page file is read on every request). */
 const [state,files]=await Promise.all([pcState(),pcFetch('/api/docs').catch(()=>pcFetch('/api/cvs').then(d=>({cvs:d.files})))]);pcDocs(files);
 await pcSearchState();
 setInterval(()=>{if(!document.hidden&&(S.view==='search'||PC_SEARCHES.some(s=>['queued','running'].includes(s.status))))pcSearchState()},2500);
 const active=state.tasks.find(t=>['queued','running'].includes(t.status));
 if(active)pcResume(active.id,active.kind).catch(e=>toast(e.message,null,true));
}
function pcPause(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function pcResume(id,kind){
 if(!OP)startOp(kind,CUR?CUR._id:'',null);
 try{for(;;){const state=await pcState(),task=state.tasks.find(t=>t.id===id);
  if(!task)throw Error('This task is no longer listed. Reload the page.');
  if(task.status==='done'){const r=POSTINGS.find(r=>r._id===task.result);if(r){CUR=r;renderResult(r)}return r}
  if(['failed','interrupted'].includes(task.status))throw Error(task.message);
  setOp(task.message+' You can close the page and return later.',true);await pcPause(1500);
 }}catch(e){$('#r-err').textContent=e.message;$('#r-err').hidden=false;throw e}finally{endOp()}
}
async function pcSubmit(payload){
 const data=await pcFetch('/api/ai/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,requestId:crypto.randomUUID()})});
 return pcResume(data.id,payload.kind);
}
analyze=async function(){
 if(OP)return;await IMGQ;$('#r-err').hidden=true;
 /* The sentiment step opens after this analysis only when "Write a cover letter" started it for this job. */
 if(PC_LETTER&&PC_LETTER!==REVIEW_JOB)PC_LETTER='';
 const posting={company:$('#r-company').value.trim(),role:$('#r-role').value.trim(),link:$('#r-link').value.trim(),text:$('#r-text').value.trim(),jobId:REVIEW_JOB||''};
 startOp('analyze','',null);
 try{const images=[];for(const image of await prepImages()){images.push(await new Promise((resolve,reject)=>{const r=new FileReader;r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(image)}))}
 const result=await pcSubmit({kind:'analyze',posting,images});if(result){$('#f-review').reset();clearImages();REVIEW_JOB='';store.set('rdraft',null);$('#r-msg').textContent='Analysis saved. Choose a document below if you want one.';$('#result').scrollIntoView({block:'start'});
  /* "Write a cover letter" from a job: the sentiment step opens next. Nothing is written until he answers it. */
  if(PC_LETTER){PC_LETTER='';CUR=result;openSent()}}
 else PC_LETTER='';
 }catch(e){PC_LETTER='';$('#r-err').textContent=e.message;$('#r-err').hidden=false}finally{if(OP)endOp()}
};
makeDoc=async function(kind,sentiment=''){
 if(OP||!CUR)return;startOp(kind,CUR._id,null);
 try{await pcSubmit({kind,postingId:CUR._id,sentiment,withoutSentiment:kind==='letter'&&!sentiment.trim()});if(kind==='letter')SENT.id='';renderResult(CUR)}
 catch(e){toast(e.message,null,true)}finally{if(OP)endOp()}
};
/* Downloads stay on the page: fetch, then save the blob. A failure shows a message; the page never navigates away. */
const PCDL=new Set();
async function pcDownload(url,fallback,button){if(PCDL.has(url))return;PCDL.add(url);if(button)button.disabled=true;
 try{const r=await fetch(url,{cache:'no-store'});if(r.status===401){location.assign('/login');return}
  if(!r.ok)throw Error(r.status===404?'That file is not available. Reload the page; if it persists, HQ on the PC may need a restart.':'Could not download this file. Try again.');
  const blob=await r.blob(),href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=(/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition')||'')||[])[1]||fallback;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(href),30000)}
 catch(x){toast(x.message||'Could not download this file.',null,true)}finally{PCDL.delete(url);if(button)button.disabled=false}}
downloadCv=async function(id){return pcDownload('/api/cvs/'+encodeURIComponent(id),'CV.docx',null)};
/* "Review fit + draft CV" never inherits a pending "Write a cover letter". */
const pcReviewFromJob=reviewFromJob;reviewFromJob=function(j){PC_LETTER='';return pcReviewFromJob(j)};
docBlock=function(title,which,text,extra=''){return `<section class="sec" data-sec="${which}"><div class="doc-h"><h3>${title}</h3><button class="btn ghost" type="button" data-doc="${which}" data-do="copy">Copy</button><button class="btn ghost" type="button" data-pc-doc="${which}">Download draft .docx</button></div><pre class="doc">${esc(text)}</pre>${extra}</section>`};
document.addEventListener('click',async e=>{const b=e.target.closest('[data-pc-doc]');if(!b||!CUR)return;e.stopImmediatePropagation();b.disabled=true;
 try{const r=await fetch('/api/ai/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:CUR._id,kind:b.dataset.pcDoc==='cv'?'cv':'letter'})});
 if(!r.ok)throw Error('Could not export this draft. Sign in again if your session ended.');
 const blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=(/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition')||'')||[])[1]||'HQ_DRAFT.docx';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
 }catch(x){toast(x.message,null,true)}finally{b.disabled=false}},true);
/* ---------- Documents tab: CVs, cover letters, reports. Claude · HQ Builder (Opus), 2026-11-05. ----------
   Lists come from /api/docs (allowlisted files in work/). Downloads are fetched and saved as blobs; a report is
   read as data blocks and built here with textContent only, never innerHTML of file content. */
const pcPairs=()=>new Set([...CVS,...CLS].map(d=>d.kind+':'+d.pair));
function pcDocRow(d,other){const has=pcPairs().has(other+':'+d.pair);
 return `<li>${av(d.company,'sm')}<span class="t"><b>${esc(d.title)}</b><small>${esc(pcDate(d.updated))}${has?` · <span class="chip g">${other==='cl'?'Has a cover letter':'Has a CV'}</span>`:''}${d.clean===false?` · <span class="chip a" title="A line such as Sentiment:, Signed : or [DRAFT] is in this file. Check it before sending.">Has notes</span>`:''}</small></span><button class="btn pc-dl" type="button" data-pc-file="${esc(d.id)}" data-kind="${esc(d.kind)}" aria-label="Download ${esc(d.title)} ${d.kind==='cl'?'cover letter':'CV'}">${IC.dl}<span>Download</span></button></li>`}
function pcDocs(data){
 CVS=(Array.isArray(data.cvs)?data.cvs:[]).slice().sort((a,b)=>String(b.updated).localeCompare(String(a.updated))||String(a.title).localeCompare(String(b.title)));
 CLS=(Array.isArray(data.letters)?data.letters:[]).slice().sort((a,b)=>String(b.updated).localeCompare(String(a.updated))||String(a.title).localeCompare(String(b.title)));
 REPORTS=Array.isArray(data.reports)?data.reports:[];
 $('#cvs').innerHTML=CVS.length?`<ul class="cvl">${CVS.map(d=>pcDocRow(d,'cl')).join('')}</ul>`:'<p class="mut">No CV files yet.</p>';
 $('#cls').innerHTML=CLS.length?`<ul class="cvl">${CLS.map(d=>pcDocRow(d,'cv')).join('')}</ul>`:'<p class="mut">No cover letter files yet.</p>';
 $('#reports').innerHTML=REPORTS.length?`<ul class="cvl">${REPORTS.map(r=>`<li><span class="t"><b>${esc(r.title)}</b><small>${esc(pcDate(r.updated))}</small></span><span class="pc-acts"><button class="btn" type="button" data-pc-read="${esc(r.id)}">Read</button><button class="btn ghost pc-dl" type="button" data-pc-report="${esc(r.id)}" data-fmt="md">${IC.dl}<span>.md</span></button><button class="btn ghost pc-dl" type="button" data-pc-report="${esc(r.id)}" data-fmt="docx">${IC.dl}<span>.docx</span></button></span></li>`).join('')}</ul>`:'<p class="mut">No reports yet.</p>';
 if(READY&&($('#drawer').classList.contains('on')||S.view==='list'))render()}
gotCvs=function(files){pcDocs({cvs:files,letters:CLS,reports:REPORTS})};
/* A job's cover letter: same company rule as its CV (first word of the file suffix), and the CV's own pair first. */
function clFor(j){const k=c=>alnum(c.company),cv=cvFor(j);
 const hits=CLS.filter(c=>k(c)&&alnum(j.Company).startsWith(k(c)));
 return hits.find(c=>cv&&c.pair===cv.pair)||hits.sort((a,b)=>cvMatch(j,b)-cvMatch(j,a))[0]}
function pcJobDocs(j){const cl=clFor(j);
 return `${cl?`<button class="btn ghost" type="button" data-pc-file="${esc(cl.id)}" data-kind="cl">${IC.dl}Download cover letter (.docx)</button>`:''}<button class="btn ghost" type="button" data-pc-letter="${esc(j._id)}">Write a cover letter</button>`}
function pcWriteLetter(j){
 const done=POSTINGS.filter(p=>p.jobId===j._id).sort((a,b)=>String(b.createdAt||'').localeCompare(String(a.createdAt||'')))[0];
 if(done){if($('#drawer').classList.contains('on')&&closeDrawers()===false)return;S.view='review';render();CUR=done;openSent();return}
 reviewFromJob(j);if(S.view!=='review')return;PC_LETTER=j._id;
 const h=$('#r-hint');h.textContent='Writing a cover letter for '+(j.Company||'this job')+'. Paste the posting text or add a photo, then tap Analyze. Next you choose what the letter should transmit; nothing is written before that.';h.hidden=false}
function pcRuns(el,runs){for(const r of Array.isArray(runs)?runs:[]){const t=String(r[0]??'');if(r[1]){const b=document.createElement('b');b.textContent=t;el.append(b)}else el.append(document.createTextNode(t))}}
function pcReport(view){const box=$('#report-view');box.replaceChildren();
 const head=document.createElement('div');head.className='doc-h';const h=document.createElement('h3');h.textContent=view.title||view.name;head.append(h);
 const mk=(label,attrs)=>{const b=document.createElement('button');b.type='button';b.className='btn ghost';b.textContent=label;for(const[k,v]of Object.entries(attrs))b.setAttribute(k,v);head.append(b)};
 mk('Download .md',{'data-pc-report':view.id,'data-fmt':'md'});mk('Download .docx',{'data-pc-report':view.id,'data-fmt':'docx'});mk('Close',{'data-pc-close':'1'});
 const body=document.createElement('div');body.className='rep-body';
 for(const b of Array.isArray(view.blocks)?view.blocks:[]){let n;
  if(b.type==='h'){n=document.createElement('h'+(Math.min(3,Math.max(1,+b.level||1))+2));pcRuns(n,b.runs)}
  else if(b.type==='p'){n=document.createElement('p');pcRuns(n,b.runs)}
  else if(b.type==='ul'){n=document.createElement('ul');for(const it of b.items||[]){const li=document.createElement('li');pcRuns(li,it);n.append(li)}}
  else if(b.type==='table'){n=document.createElement('ul');n.className='rep-rows';for(const row of b.rows||[]){const li=document.createElement('li');
   for(const c of row){const line=document.createElement('div');if(c.label){const l=document.createElement('b');l.textContent=c.label+': ';line.append(l)}pcRuns(line,c.runs);li.append(line)}n.append(li)}}
  if(n)body.append(n)}
 box.append(head,body);box.hidden=false;box.scrollIntoView({block:'start'})}
document.addEventListener('click',async e=>{const t=e.target.closest('[data-pc-file],[data-pc-report],[data-pc-read],[data-pc-close],[data-pc-letter]');if(!t)return;e.stopImmediatePropagation();e.preventDefault();
 /* A CV goes through /api/cvs, which every HQ version serves; letters and reports need the Documents routes. */
 if(t.dataset.pcFile)return pcDownload((t.dataset.kind==='cl'?'/api/docs/':'/api/cvs/')+encodeURIComponent(t.dataset.pcFile),t.dataset.kind==='cl'?'Cover_letter.docx':'CV.docx',t);
 if(t.dataset.pcReport){const f=t.dataset.fmt==='docx'?'docx':'md';return pcDownload('/api/reports/'+encodeURIComponent(t.dataset.pcReport)+'/'+f,'report.'+f,t)}
 if(t.dataset.pcClose){$('#report-view').hidden=true;return}
 if(t.dataset.pcLetter){const j=byKey(t.dataset.pcLetter);if(j)pcWriteLetter(j);return}
 t.disabled=true;try{pcReport(await pcFetch('/api/reports/'+encodeURIComponent(t.dataset.pcRead)))}catch(x){toast(x.message,null,true)}finally{t.disabled=false}},true);
/* On-demand discovery is separate from document tasks. Results never enter the workbook until the owner saves a lead. */
let PC_SEARCHES=[],PC_SEARCH_BUSY=false,PC_SEARCH_FETCH=false,PC_SEARCH_STAMP='';
function pcSearchLink(value){try{const u=new URL(String(value||''));return u.protocol==='https:'&&!u.username&&!u.password?u.href:''}catch{return ''}}
function pcSearchKey(value){const u=pcSearchLink(value);return u?u.replace(/#.*$/,'').replace(/\/$/,''):''}
function pcSearchJob(result){const link=pcSearchKey(result.link);return link?D.jobs.find(j=>pcSearchKey(j.Link)===link):null}
function pcSearchError(message){const el=$('#s-err');el.textContent=message;el.hidden=false}
function pcRenderSearch(data){
 PC_SEARCHES=Array.isArray(data.searches)?data.searches:[];
 const sources=Array.isArray(data.sources)?data.sources:[],scope=$('#s-sources');
 if(scope)scope.textContent=sources.length?'Searches these company boards: '+sources.map(s=>s.label).join(', ')+'. Other employers are not included.':'Searches the configured company boards. Other employers are not included.';
 const stamp=JSON.stringify([PC_SEARCHES,D.jobs.map(j=>[j._id,j.Link])]);if(stamp===PC_SEARCH_STAMP)return;PC_SEARCH_STAMP=stamp;
 const active=PC_SEARCHES.some(s=>['queued','running'].includes(s.status));$('#s-go').disabled=PC_SEARCH_BUSY||active;
 $('#s-go').textContent=active?'Search running…':'Search now';
 const labels={queued:['Queued','a'],running:['Searching','b'],completed:['Finished','g'],failed:['Failed','r'],interrupted:['Interrupted','a']};
 $('#requests').innerHTML=PC_SEARCHES.length?PC_SEARCHES.map(s=>{const label=labels[s.status]||['Status unavailable','a'],results=Array.isArray(s.results)?s.results:[],errors=Array.isArray(s.sourceErrors)?s.sourceErrors:[];
  return `<article class="rq pc-search"><div class="rq-h"><h3>${esc(s.query||'Job search')}</h3><span class="chip ${label[1]}"><i class="dot"></i>${label[0]}</span></div>
   <p class="d-meta">${[s.location,s.remote?'Remote only':'',pcDate(s.created)].filter(Boolean).map(x=>`<span>${esc(x)}</span>`).join('')}</p>
   <p class="pc-search-message" role="status">${esc(s.message||'Waiting for an update.')}</p>
   ${Number.isInteger(s.sourcesChecked)&&Number.isInteger(s.sourcesTotal)?`<p class="mut">Company boards checked: ${s.sourcesChecked} of ${s.sourcesTotal}.</p>`:''}
   ${errors.length?'<p class="banner a">Some company boards could not be checked. These results cover only the boards that responded; try again later.</p>':''}
   ${s.truncated?'<p class="banner a">Only the first matching jobs are shown. Add a more specific role keyword or location to narrow your search.</p>':''}
   ${results.length?`<p class="mut">${results.length} result${results.length===1?'':'s'} · No fit score yet. Check each posting for eligible countries and requirements.</p><ul class="pc-results">${results.map(r=>{const job=pcSearchJob(r),link=pcSearchLink(r.link);return `<li><div class="pc-result-head"><div class="t"><h4>${esc(r.role||'Role not listed')}</h4><p><b>${esc(r.company||'Company not listed')}</b>${r.location?' · '+esc(r.location):''}</p></div>${job?'<span class="chip g">In your tracker</span>':''}</div>
    ${r.summary?`<p class="pc-result-summary">${esc(r.summary)}</p>`:''}<p class="mut pc-result-source">Source: ${esc(r.source||'Company job board')}${r.checkedAt?' · Checked '+esc(pcDate(r.checkedAt)):''}</p>
    <div class="pc-result-actions">${link?`<a class="btn ghost" href="${esc(link)}" target="_blank" rel="noopener noreferrer">Open posting<span class="sr"> (opens a new tab)</span></a>`:'<span class="mut">Posting link unavailable</span>'}<button class="btn" type="button" data-pc-search="${esc(s.id)}" data-pc-result="${esc(r.id)}"${!link?' disabled':''}>${job?'View in tracker':'Add to tracker'}</button></div></li>`}).join('')}</ul>`:s.status==='completed'?'<p class="mut">No matching jobs on these boards. Try fewer role keywords, clear the location, or turn off Remote only.</p>':''}
   ${['failed','interrupted'].includes(s.status)?`<button class="btn ghost" type="button" data-pc-retry="${esc(s.id)}">Use this search again</button>`:''}</article>`}).join(''):'<p class="mut">No searches yet. Search now to check the listed company boards; results stay here until you choose to add a lead.</p>';
}
async function pcSearchState(){if(PC_SEARCH_FETCH)return;PC_SEARCH_FETCH=true;
 try{const data=await pcFetch('/api/search/state');pcRenderSearch(data);if($('#s-err').dataset.connection==='1'){$('#s-err').hidden=true;delete $('#s-err').dataset.connection}return data}
 catch(e){$('#s-err').dataset.connection='1';pcSearchError(e.message+' Your previous results are kept. Reload or try again when the server is available.');if(!PC_SEARCH_STAMP)$('#requests').textContent='Search results could not be loaded.'}
 finally{PC_SEARCH_FETCH=false}}
async function pcSubmitSearch(){if(PC_SEARCH_BUSY)return;const query=$('#s-what').value.trim(),location=$('#s-where').value.trim();$('#s-err').hidden=true;
 if(query.length<2){pcSearchError('Add a role keyword, such as analyst or automation.');$('#s-what').focus();return}
 PC_SEARCH_BUSY=true;$('#s-go').disabled=true;$('#s-msg').textContent='Starting your search…';
 try{await pcFetch('/api/search/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,location,remote:$('#s-remote').checked,requestId:crypto.randomUUID()})});
  $('#s-msg').textContent='Search started. You can close the page and return later. Nothing is added to the tracker yet.';await pcSearchState()}
 catch(e){pcSearchError(e.message);$('#s-msg').textContent='Search was not confirmed. Check Your searches below before trying again.'}
 finally{PC_SEARCH_BUSY=false;$('#s-go').disabled=PC_SEARCHES.some(s=>['queued','running'].includes(s.status));$('#s-go').textContent=$('#s-go').disabled?'Search running…':'Search now'}
}
$('#f-search').addEventListener('submit',e=>{e.preventDefault();e.stopImmediatePropagation();pcSubmitSearch()},true);
$('#requests').setAttribute('aria-live','polite');
$('#requests').addEventListener('click',e=>{const retry=e.target.closest('[data-pc-retry]');if(retry){const s=PC_SEARCHES.find(x=>x.id===retry.dataset.pcRetry);if(s){$('#s-what').value=s.query||'';$('#s-where').value=s.location||'';$('#s-remote').checked=!!s.remote;$('#s-msg').textContent='Search loaded. Tap Search now to run it again.';$('#f-search').scrollIntoView({block:'start'})}return}
 const b=e.target.closest('[data-pc-result]');if(!b)return;const s=PC_SEARCHES.find(x=>x.id===b.dataset.pcSearch),r=s?.results?.find(x=>x.id===b.dataset.pcResult);if(!r)return;
 const job=pcSearchJob(r);if(job){openJob(job._id);return}
 if(!READY||!D.sheets.length){toast('Your job lists are not available yet. Reload the spreadsheet first.',null,true);return}
 if(PC_WAITING){toast('An edit is waiting. Check the save message at the top before adding another lead.',null,true);return}
 openAdd();if(!$('#addDlg').open)return;const f=$('#addForm'),put=(name,value)=>{const el=f.elements.namedItem(name);if(el)el.value=String(value||'')};
 put('Company',r.company);put('Role',r.role);put('Where',r.location);put('Link',pcSearchLink(r.link));put('Source',r.source||'Company job board');
 put('My status',STAGE_DATA.new_row);
 put('Notes','Source: '+(r.source||'Company job board')+'\nChecked: '+pcDate(r.checkedAt)+'\nNext step: Read the posting and check the eligible countries before applying.');
 $('#addErr').textContent='Check the list and details, then tap Add lead to save this job.';$('#addErr').hidden=false;
});
$('.seg').addEventListener('click',e=>{if(e.target.closest('[data-v="search"]'))pcSearchState()});
