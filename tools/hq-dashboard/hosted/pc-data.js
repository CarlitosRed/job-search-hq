/* PC backend. Workbook remains the source of truth. */
let busy=false,READY=false,seenView=null;
const CAP={db:null,sample:null,downloads:null},NOTED=new Set(),TOOL_VIEWS=['search','review'];
const nowISO=()=>new Date().toISOString();
const use=()=>Promise.resolve(null);
function pcDate(value){const s=String(value||'');if(!/^\d{16,20}$/.test(s))return s;const d=new Date(Number(s)/1e6);return Number.isNaN(d.getTime())?'Date unavailable':d.toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'})}
let PC_WAITING=false,PC_LAST_WRITE='';
async function pcHealth(){try{const h=await pcFetch('/api/health');let el=document.getElementById('pc-health');if(!el){el=document.createElement('small');el.id='pc-health';el.style.display='block';$('#sync').after(el)}el.textContent=h.scan?.message||'Scan status unavailable.'}catch{}}
// Signed: Codex (builder), 2026-11-07. Save notices are UI state only.
let PC_SAVE_STAMP='',PC_SAVE_TIMER,PC_SAVE_FADE,PC_SAVE_ERROR=false,PC_SAVE_POINTER=false;
function pcSaveHold(box){return PC_SAVE_POINTER||box.contains(document.activeElement)}
function pcSaveStop(box){clearTimeout(PC_SAVE_TIMER);clearTimeout(PC_SAVE_FADE);box.classList.remove('pc-save-fade')}
function pcSaveArm(box){
 pcSaveStop(box);if(PC_SAVE_ERROR||box.hidden||pcSaveHold(box))return;
 PC_SAVE_TIMER=setTimeout(()=>{
  if(pcSaveHold(box))return;
  if(matchMedia('(prefers-reduced-motion: reduce)').matches){box.hidden=true;return}
  box.classList.add('pc-save-fade');
  PC_SAVE_FADE=setTimeout(()=>{if(!pcSaveHold(box))box.hidden=true},180);
 },5000);
}
function pcSaveNotice(stamp,message,error=false,edit=null){
 let box=document.getElementById('pc-saves');
 if(!box){
  box=document.createElement('aside');box.id='pc-saves';box.hidden=true;
  box.addEventListener('pointerenter',()=>{PC_SAVE_POINTER=true;pcSaveStop(box)});
  box.addEventListener('pointerleave',()=>{PC_SAVE_POINTER=false;pcSaveArm(box)});
  box.addEventListener('focusin',()=>pcSaveStop(box));
  box.addEventListener('focusout',()=>setTimeout(()=>pcSaveArm(box),0));
  document.querySelector('.top').after(box);
 }
 // Identical five-second polls must neither restart the timer nor revive a dismissed notice.
 if(stamp===PC_SAVE_STAMP)return;
 PC_SAVE_STAMP=stamp;PC_SAVE_ERROR=error;pcSaveStop(box);
 box.className=error?'banner':'banner n';box.setAttribute('role',error?'alert':'status');
 box.setAttribute('aria-live',error?'assertive':'polite');box.setAttribute('aria-atomic','true');
 box.hidden=!message;if(!message){box.replaceChildren();return}
 const text=document.createElement('span');text.textContent=message;
 let button=box.querySelector('button');
 if(error||edit){
  if(!button){button=document.createElement('button');button.className='btn ghost';button.type='button'}
  button.textContent=edit?(edit.status==='waiting'?'Cancel waiting edit':'Dismiss after checking the spreadsheet'):'Dismiss';
  button.onclick=async()=>{
   if(!edit){pcSaveStop(box);box.hidden=true;return}
   button.disabled=true;
   try{await pcFetch('/api/writes/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:edit.id})});await pcWrites();await reloadJobs(true)}
   catch{pcSaveNotice('unreachable','Server unreachable. Save status is unavailable. Check the spreadsheet before trying again.',true)}
   finally{button.disabled=false}
  };
  // Reuse the button so a polling update does not discard keyboard focus.
  for(const child of [...box.childNodes])if(child!==button)child.remove();
  box.prepend(text);if(!button.parentNode)box.append(button);
 }else box.replaceChildren(text);
 pcSaveArm(box);
}
async function pcWrites(){
 try{const data=await pcFetch('/api/writes'),items=data.writes||[];
 const pending=items.filter(w=>['waiting','applying'].includes(w.status));
 const errors=items.filter(w=>['conflict','needs_check','failed'].includes(w.status));
 // Preserve the queue's existing edit-blocking policy, including across connection loss.
 PC_WAITING=items.some(w=>['waiting','applying','conflict','needs_check'].includes(w.status));
 const w=errors[0]||pending[0]||items[0];
 const relevant=errors.length||pending.length?[...errors,...pending]:w?[w]:[];
 const stamp=JSON.stringify(relevant.map(w=>[w.id,w.status,w.label,w.message]));
 const summary=[];
 if(errors.length>1)summary.push(`${errors.length} edits need attention.`);
 if(pending.length>1||errors.length&&pending.length)summary.push(`${pending.length} edit${pending.length===1?'':'s'} waiting or saving.`);
 const message=[...summary,...(w?[w.label+': '+w.message]:[])].join(' ');
 pcSaveNotice(stamp,message,!!errors.length,w&&['waiting','conflict','needs_check','failed'].includes(w.status)?w:null);
 const saved=items.filter(w=>w.status==='saved').map(w=>w.id).join(':');
 if(PC_LAST_WRITE&&PC_LAST_WRITE!==saved&&saved)await reloadJobs(true);
 PC_LAST_WRITE=saved||'none';
 }catch{pcSaveNotice('unreachable','Server unreachable. Save status is unavailable. Check the spreadsheet before trying again.',true)}
}
function checkText(j){return String(j._check||'')}
async function pcFetch(path,options={}){
 const r=await fetch(path,{cache:'no-store',...options});
 if(r.status===401){location.assign('/login');throw Error('Please sign in again.')}
 const d=await r.json();if(!r.ok)throw Error(d.error||'Could not complete this change.');return d;
}
async function reloadJobs(silent=false){
 try{const d=await pcFetch('/api/data');Object.assign(D,d);
 D.jobs.forEach(j=>{enrich(j);if(NOTED.has(j._id))j._noteSaved=true});
 linkSiblings();READY=true;fillAdd();render();syncLabel();
 if(!silent)toast('Reloaded from your spreadsheet');
 }catch(e){$('#sync').textContent='Connection lost. Your unsaved edits are still here.';if(!silent)toast(e.message,null,true)}
}
function syncLabel(){ $('#sync').textContent=`${D.jobs.length} jobs · spreadsheet connected`; }
async function boot(){
 $('#requests').textContent='Search automation is awaiting setup. Your existing daily scan stays in place.';
 await reloadJobs(true);
 await pcWrites();
 await pcHealth();
 await pcInit();
 const b=document.createElement('button');b.className='btn ghost';b.textContent='Sign out';
 b.onclick=async()=>{await pcFetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});location.assign('/login')};
 $('#sync').parentNode.append(b);
 setInterval(async()=>{if(busy||document.hidden)return;await pcWrites();await pcHealth();try{const d=await pcFetch('/api/version');if(d.version!==D.version)await reloadJobs(true)}catch{}},5000);
}
async function post(url,p){
 try{
 if(PC_WAITING){toast('An edit is waiting. Check the save message at the top first.',null,true);return null}
 const j=p.id?(p.sheet==='Contacts'?D.contacts:D.jobs).find(j=>j._id===p.id):null;
 const payload={...p,version:D.version,requestId:crypto.randomUUID()};
 if(j){payload.row=j._row;payload.expect=p.sheet==='Contacts'?{Person:j.Person}:{Company:j.Company,Role:j.Role}}
 const d=await pcFetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 if(d.queued){await pcWrites();toast('Your edit is waiting for Excel. It is not saved to the spreadsheet yet.',null,true);return null}
 D.version=d.version;
 await pcWrites();
 if(url==='/api/add')await reloadJobs(true);
 return d;
 }catch(e){await pcWrites();toast(e.message,null,true);return null}
}
function openSoon(id){if(byKey(id))openJob(id)}
