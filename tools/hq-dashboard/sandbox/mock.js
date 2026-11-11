/* Sandbox stand-in for the claude.ai runtime (db, sample, downloads).
   Everything stays in this tab's memory; nothing reaches claude.ai. window.__log records every write, download and Claude call
   so testers can check what the page did; window.__prompts holds each prompt sent. It follows the real contracts: sample rejects
   plain {code,message}, honours signal, checks images against limits(); db update deep-merges nested objects, arrays and null replace.
   URL switches: ?signedout, ?slow (1.5 s latency), ?wait=MS (a Claude call takes MS longer), ?noimages (no images in limits),
   ?notposting, ?unreadable, ?fail=CODE (every Claude call rejects CODE; add &failstep=analyze|cv|letter for one step, &once to fail
   one time only), ?dbfail=CODE (every write rejects CODE; &once applies here too). Change the same switches live on window.__mock. */
(function(){
 const SEED=window.__SEED,L={},q=new URLSearchParams(location.search),lag=q.has("slow")?1500:40;
 const M=window.__mock={fail:q.get("fail")||"",failstep:q.get("failstep")||"",once:q.has("once"),dbfail:q.get("dbfail")||"",notposting:q.has("notposting"),unreadable:q.has("unreadable"),wait:+q.get("wait")||0,images:!q.has("noimages")};
 const wait=ms=>new Promise(r=>setTimeout(r,ms));window.__log=[];window.__prompts=[];
 const clone=o=>JSON.parse(JSON.stringify(o));
 const freeze=o=>{if(o&&typeof o==="object"){Object.values(o).forEach(freeze);Object.freeze(o)}return o};
 const isObj=v=>!!v&&typeof v==="object"&&!Array.isArray(v);
 const merge=(a,b)=>{const o={...a};for(const k of Object.keys(b))o[k]=isObj(b[k])&&isObj(o[k])?merge(o[k],b[k]):b[k];return o};
 const dbErr=()=>{if(!M.dbfail)return;const c=M.dbfail;if(M.once)M.dbfail="";throw{code:c,message:"sandbox: forced "+c}};
 const snap=(c,id)=>{const v=SEED[c]&&SEED[c][id];return{id,exists:!!v,data:()=>v?freeze(clone(v)):undefined,metadata:{fromCache:false,hasPendingWrites:false}}};
 const query=c=>{const docs=Object.keys(SEED[c]||{}).sort().map(id=>snap(c,id));return{docs,size:docs.length,empty:!docs.length,metadata:{fromCache:false,hasPendingWrites:false}}};
 const fire=c=>(L[c]||[]).forEach(f=>f(query(c)));
 const newId=()=>"sbx"+Math.random().toString(36).slice(2,8);
 const docRef=(c,id)=>({id,path:c+"/"+id,
  async get(){await wait(lag);return snap(c,id)},
  async set(d){await wait(lag);dbErr();(SEED[c]=SEED[c]||{})[id]=clone(d);__log.push(["set",c+"/"+id,Object.keys(d)]);fire(c)},
  async update(d){await wait(lag);dbErr();if(!SEED[c]||!SEED[c][id])throw{code:"invalid_argument",message:"missing doc"};SEED[c][id]=merge(SEED[c][id],clone(d));__log.push(["update",c+"/"+id,Object.keys(d)]);fire(c)},
  async delete(){await wait(lag);dbErr();if(SEED[c])delete SEED[c][id];__log.push(["delete",c+"/"+id]);fire(c)}});
 const db={collection:c=>({path:c,
   doc:id=>docRef(c,id||newId()),
   onSnapshot(n){(L[c]=L[c]||[]).push(n);setTimeout(()=>fire(c),lag);return()=>{L[c]=L[c].filter(f=>f!==n)}},
   async get(){await wait(lag);return query(c)},
   async add(d){await wait(lag);dbErr();const id=newId();(SEED[c]=SEED[c]||{})[id]=clone(d);__log.push(["add",c,id]);fire(c);return docRef(c,id)}}),
  doc:p=>{const[c,id]=p.split("/");return docRef(c,id)}};

 const LIMITS={maxCount:5,maxInputBytes:20000000,mediaTypes:["image/jpeg","image/png","image/webp","image/gif"]};
 const reject=(code,message)=>{throw{code,message:message||"sandbox: "+code}};
 const kindOf=p=>/Write ONE cover letter/.test(p)?"letter":/Write ONE CV/.test(p)?"cv":"analyze";
 const between=(p,a,b)=>{const i=p.indexOf(a),j=p.indexOf(b);return i<0||j<0?"":p.slice(i+a.length,j).trim()};
 let letters=0,cvs=0;
 function reply(kind,prompt,nImages){
  if(kind==="letter"){const s=between(prompt,"=====BEGIN SENTIMENT=====","=====END SENTIMENT=====");letters++;
   return{...clone(window.__LETTER),cover_letter_markdown:window.__LETTER.cover_letter_markdown.replace("{tone}",s||"plain").replace("{n}",letters),
    flags:/\b(say|claim|senior|years|title|salary|ignore)\b/i.test(s)?["The sentiment asked for more than tone: that part was left out"]:[]}}
  if(kind==="cv"){cvs++;return{...clone(window.__CV),cv_markdown:window.__CV.cv_markdown.replace("{n}",cvs)}}
  if(M.unreadable)return{is_job_posting:true,readable:false,summary:"Sandbox: the photo is too blurry to read."};
  if(M.notposting)return{is_job_posting:false,readable:true,summary:"Sandbox: this looks like a lunch menu, not a job."};
  const typed=between(prompt,"Text:\n","=====END POSTING=====");
  return{is_job_posting:true,readable:true,company:nImages?"Photo Corp":"",role:nImages?"AI Operations Analyst":"",link:nImages?"https://example.com/from-photo":"",
   posting_text:nImages?window.__POSTING_TEXT:typed,...clone(window.__FIT)}}
 async function run(input,opts={},parse){
  if(Object.prototype.toString.call(opts)!=="[object Object]")reject("invalid_request","options must be a plain object");
  const{signal,images,onText}=opts;if(signal!==undefined&&!(signal instanceof AbortSignal))reject("invalid_request","pass ctl.signal");
  if(signal&&signal.aborted)reject("cancelled");
  const prompt=typeof input==="string"?input:input.map(t=>t.content).join("\n"),kind=kindOf(prompt),list=images?[].concat(images instanceof FileList?[...images]:images):[],info=[];
  if(new TextEncoder().encode(prompt).length>262144)reject("prompt_too_large");
  if(list.length&&!M.images)reject("images_unavailable");
  if(list.length>LIMITS.maxCount)reject("image_rejected","too many images");
  for(const b of list){if(!LIMITS.mediaTypes.includes(b.type)||b.size>LIMITS.maxInputBytes)reject("image_rejected","type or size");
   let bm;try{bm=await createImageBitmap(b)}catch{reject("image_rejected","undecodable")}info.push({type:b.type,size:b.size,w:bm.width,h:bm.height});bm.close()}
  __log.push(["sample",kind,prompt.length,{modelTier:opts.modelTier,cache:opts.cache,images:info}]);__prompts.push(prompt);
  await new Promise((res,rej)=>{const t=setTimeout(res,lag*10+M.wait);if(signal)signal.addEventListener("abort",()=>{clearTimeout(t);rej({code:"cancelled",message:"aborted"})},{once:true})});
  if(M.fail&&(!M.failstep||M.failstep===kind)){const c=M.fail;if(M.once)M.fail="";reject(c,"sandbox: forced "+c)}
  const out=reply(kind,prompt,list.length),text=JSON.stringify(out);if(onText)onText({text,delta:text});return parse?out:{text,truncated:false,modelTierApplied:opts.modelTier||"default"}}
 const sample=(input,opts)=>run(input,opts,false);
 sample.json=(input,opts)=>run(input,opts,true);
 sample.limits=async()=>({maxPromptBytes:262144,...(M.images?{images:LIMITS}:{}),modelTiers:["default","complex","quick"]});
 const downloads={async save({filename,data}){const size=data&&(data.size??data.length);__log.push(["download",filename,size]);await wait(lag);return{status:"saved"}}};
 window.claude={use:async n=>q.has("signedout")?null:({db,sample,downloads})[n]||null};
})();
