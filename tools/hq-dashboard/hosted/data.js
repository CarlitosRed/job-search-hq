/* ---------- data (hosted) ----------
   The page keeps its data in its own store on claude.ai, so it opens on a computer or a phone.
   The daily {{SCAN_TIME}} run syncs the store with work/applications-tracker.xlsx (page edits win). */
let busy=false,READY=false,seenView=null;const CAP={db:null,sample:null,downloads:null},NOTED=new Set(),TOOL_VIEWS=["search","review"];
const STATUSES=["Suggested (not applied)","Message drafted","CV ready","Applied","Interview","Rejected","Offer","Skipped"];
const JOB_COLS=["#","Company","Role","Where","Posted","Score","Verdict","My status","Date applied","CV used","Source","Link","Notes"];
const pad2=n=>String(n).padStart(2,"0"),todayISO=()=>{const d=new Date();return`${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`},nowISO=()=>new Date().toISOString();
Object.assign(D,{statuses:STATUSES,today:todayISO(),file:"applications-tracker.xlsx"});
const use=n=>window.claude&&typeof window.claude.use==="function"?Promise.resolve(window.claude.use(n)).catch(()=>null):Promise.resolve(null);
function jobFrom(d){const x=d.data()||{};return{_id:d.id,"#":x.num??"",Company:String(x.company||""),Role:String(x.role||""),Where:x.where||"",Posted:x.posted||"",Score:x.score??"",Verdict:x.verdict||"",
 "My status":x.status||STATUSES[0],"Date applied":x.dateApplied||"","CV used":x.cvUsed||"",Source:x.source||"",Link:x.link||"",Notes:x.notes||"",
 _check:x.checkNote||x.check||"",_checkdate:x.checkDate||"",_sheet:x.sheet||"Added on the page",_row:x.row||"",_added:!!x.addedInPage}}
/* The open check as a person would say it; the raw line (time, API, board) stays in the tooltip. */
function checkText(j){const c=String(j._check||""),m=/^\s*(OPEN|CLOSED|REDIRECTED|UNKNOWN)\b/i.exec(c);if(!m)return c;const s=m[1].toUpperCase();
 const where=/linkedin/i.test(c)?"on LinkedIn":/employer page|employer site/i.test(c)?"on the employer's site":/greenhouse|lever|ashby|workable|workday|amazon\.jobs|smartrecruiters|api/i.test(c)?"on the employer's job board":"";
 return s==="OPEN"?`Still open${where?" "+where:""}`:s==="CLOSED"?"Posting closed":s==="REDIRECTED"?"Link now goes somewhere else: check it":"Couldn't confirm it's open"}
function contactFrom(d){const x=d.data()||{};return{_id:d.id,_row:+x.row||0,Person:x.person||"","Where now":x.whereNow||"","How you know them":x.howKnown||"",Status:x.status||"","Date sent":x.dateSent||"",Reply:x.reply||"",Notes:x.notes||""}}
let metaSheets=null;
function sheetsFrom(){const prev=JSON.stringify(D.sheets);
 D.sheets=metaSheets||[...new Set(D.jobs.filter(j=>!j._added).map(j=>j._sheet))].map(name=>({name,columns:JOB_COLS}));
 if(JSON.stringify(D.sheets)!==prev)fillAdd()}
function applyJobs(js){D.jobs=js;D.today=todayISO();js.forEach(j=>{enrich(j);if(NOTED.has(j._id))j._noteSaved=true});linkSiblings();
 if(S.sel&&!byKey(S.sel))S.sel=null;READY=true;sheetsFrom();render();syncLabel()}
function watch(path,next){const t=setTimeout(()=>{$("#sync").textContent=READY?"May be out of date: can't reach your data":"Can't reach your data yet…"},10000);
 return CAP.db.collection(path).onSnapshot(s=>{if(!s.metadata.fromCache){clearTimeout(t);if(READY)syncLabel()}next(s)},
  e=>{clearTimeout(t);console.error("db "+path,e);$("#sync").textContent="Lost the connection to your data. Reload the page."})}
async function boot(){try{localStorage.removeItem("jshq.cache")}catch{}
 const [db,sample,downloads]=await Promise.all(["db","sample","downloads"].map(use));Object.assign(CAP,{db,sample,downloads});
 if(!db){$("#main").innerHTML=emptyBox("Open this page on claude.ai","Your jobs load only when you're signed in to claude.ai: a computer browser or the Claude app on your phone.","");$("#sync").textContent="Not signed in";
  $("#requests").innerHTML=$("#postings").innerHTML=$("#cvs").innerHTML=`<p class="mut">Sign in to claude.ai to see these.</p>`;return}
 watch("meta",s=>{const m=s.docs.find(d=>d.id==="main");const v=m&&m.data().sheets;metaSheets=Array.isArray(v)&&v.length?v:null;if(READY)sheetsFrom();
  const c=s.docs.find(d=>d.id==="cvs");gotCvs(c?c.data().files:[])});
 watch("jobs",s=>applyJobs(s.docs.map(jobFrom)));
 watch("contacts",s=>{D.contacts=s.docs.map(contactFrom).sort((a,b)=>a._row-b._row);if(READY&&S.view==="contacts")render()});
 watch("requests",s=>renderRequests(s.docs.map(d=>({...d.data(),_id:d.id}))));
 watch("postings",s=>gotPostings(s.docs.map(d=>({...d.data(),_id:d.id}))))}
function syncLabel(){const t=new Date();$("#sync").textContent=`${D.jobs.length} jobs · ${innerWidth>900?"saved online · ":""}synced ${t.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}`}
async function reloadJobs(){if(!CAP.db)return toast("Not connected to your data.",null,true);
 try{const s=await CAP.db.collection("jobs").get();applyJobs(s.docs.map(jobFrom));toast("Reloaded")}catch{toast("Couldn't reload. Check your connection.",null,true)}}
function dbFail(e){toast(e&&e.code==="quota_exceeded"?"This page's storage is full. Nothing was saved.":"Couldn't save. Check your connection and try again.",null,true);return null}
async function dbUpdate(path,data){if(!CAP.db)return dbFail();try{await CAP.db.doc(path).update(data);return{ok:true}}catch(e){console.error(e);return dbFail(e)}}
/* Same call shape as the local tracker's server, so the views and the undo code stay as they are. */
function post(url,p){if(url==="/api/add")return addJob(p);
 if(p.sheet==="Contacts"){const d={changedInPage:true,changedAt:nowISO()};if("status"in p)d.status=p.status;if("date"in p)d.dateSent=p.date;return dbUpdate("contacts/"+p.id,d)}
 const d={};if("status"in p||"date"in p)Object.assign(d,{statusChangedInPage:true,statusChangedAt:nowISO()});
 if("status"in p)d.status=p.status;if("date"in p)d.dateApplied=p.date;if("note"in p)Object.assign(d,{notes:p.note,notesChangedInPage:true,notesChangedAt:nowISO()});
 return dbUpdate("jobs/"+p.id,d)}
async function addJob(p){if(!CAP.db)return dbFail();
 const d={num:"",company:p.Company,role:p.Role,where:p.Where||"",posted:p.Posted||"",score:p.Score||"",verdict:p.Verdict||"",status:p["My status"]||STATUSES[0],
  dateApplied:p["Date applied"]||"",cvUsed:"",source:p.Source||"",link:p.Link||"",notes:p.Notes||"",check:"",checkNote:"",checkDate:"",sheet:p.sheet,row:"",addedInPage:true,createdAt:nowISO()};
 try{const ref=await CAP.db.collection("jobs").add(d);return{ok:true,id:ref.id,row:"new",saved:{...p}}}catch(e){console.error(e);return dbFail(e)}}
function openSoon(id,n=0){if(byKey(id)){if($("#drawer.on textarea[data-dirty='1']"))return toast("Lead added. Your unsaved notes are still open here, so the new job wasn't opened.");return openJob(id)}if(n<20)setTimeout(()=>openSoon(id,n+1),150)}

