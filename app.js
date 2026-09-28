/* DOGFOOD demo: real in-browser verification (sha256 + Ed25519) of a real signed bundle,
   a tamper control, an in-browser recompute, plus the theme toggle and the category menu. */
(function(){
const D = window.DEMO, b = D.bundle, pl = b.payload;
const $ = (s,r=document)=>r.querySelector(s);
const enc = new TextEncoder();
const esc = s=>{const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;};
const short = h=>h.slice(0,10)+'…'+h.slice(-6);

// theme toggle (first paint already set by the head script)
const root = document.documentElement;
$('#theme').addEventListener('click',()=>{
  const t = root.getAttribute('data-theme')==='dark'?'light':'dark';
  root.setAttribute('data-theme',t); try{localStorage.setItem('theme',t);}catch(e){}
});

// category menu: hover, focus, tap, keyboard, mobile
const cats = [...document.querySelectorAll('#menu > li[data-cat]')];
const isMobile = ()=>matchMedia('(max-width:900px)').matches;
const openLi = li=>{li.classList.add('open');li.querySelector('button').setAttribute('aria-expanded','true');};
const closeLi = li=>{li.classList.remove('open');li.querySelector('button').setAttribute('aria-expanded','false');};
const closeAll = ex=>cats.forEach(li=>{if(li!==ex)closeLi(li);});
cats.forEach(li=>{
  const btn = li.querySelector('button');
  li.addEventListener('mouseenter',()=>{if(!isMobile()){closeAll(li);openLi(li);}});
  li.addEventListener('mouseleave',()=>{if(!isMobile())closeLi(li);});
  btn.addEventListener('click',e=>{e.preventDefault();li.classList.contains('open')?closeLi(li):(closeAll(li),openLi(li));});
  li.querySelectorAll('.sub a').forEach(a=>a.addEventListener('click',()=>{closeLi(li);$('#menu').classList.remove('mobile');}));
});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAll();});
document.addEventListener('click',e=>{if(!e.target.closest('#menu'))closeAll();});
$('#burger').addEventListener('click',()=>$('#menu').classList.toggle('mobile'));

// static facts
$('#v-winner').textContent = D.ranking[0].title+' ('+D.ranking[0].project+')';
$('#v-method').textContent = pl.method;
$('#v-pub').textContent = short(b.public_key);
$('#v-sig').textContent = short(b.signature);
$('#stat-k').textContent = D.methodParams.k.toFixed(2);
$('#foot-commit').textContent = D.commit.slice(0,10);
$('#rc-n').textContent = D.nScores;
// what the bundle holds
const bf = $('#bundle-fields');
[['raw_scores', D.nScores+' reviews, duplicates merged'],
 ['rubric.weights','functionality 0.4, quality 0.4, innovation 0.2'],
 ['method', pl.method],
 ['method_params','mu '+D.methodParams.mu.toFixed(2)+', k '+D.methodParams.k.toFixed(2)],
 ['code_commit', D.commit.slice(0,10)],
 ['ranking', D.ranking.length+' projects'],
 ['digest','sha256 over the canonical payload'],
 ['public_key + signature','Ed25519, verifiable with no key of ours']
].forEach(([k,v])=>{const li=document.createElement('li');li.innerHTML='<span class="mono">'+k+'</span>: '+esc(v);bf.appendChild(li);});
// APPEND-1
// ---- real verification: sha256 digest + Ed25519 signature over the canonical bytes
const hexToBytes = h=>{const a=new Uint8Array(h.length/2);for(let i=0;i<a.length;i++)a[i]=parseInt(h.substr(i*2,2),16);return a;};
async function sha256hex(bytes){const d=await crypto.subtle.digest('SHA-256',bytes);return [...new Uint8Array(d)].map(x=>x.toString(16).padStart(2,'0')).join('');}
let edKey;
async function edVerify(msg){
  if(edKey===undefined){try{edKey=await crypto.subtle.importKey('raw',hexToBytes(b.public_key),{name:'Ed25519'},false,['verify']);}catch(e){edKey=null;}}
  if(!edKey) return null;
  try{return await crypto.subtle.verify({name:'Ed25519'},edKey,hexToBytes(b.signature),msg);}catch(e){return false;}
}
function setCheck(sel,state){const el=$(sel),m=el.querySelector('.m');el.classList.remove('pass','fail','idle');
  if(state==='na'){el.classList.add('idle');m.textContent='!';}
  else{el.classList.add(state?'pass':'fail');m.textContent=state?'✓':'✗';}}
async function runVerify(canonStr){
  const bytes = enc.encode(canonStr);
  const dig = await sha256hex(bytes);
  const digOk = dig===b.digest;
  const sigOk = await edVerify(bytes);
  setCheck('#chk-digest',digOk);
  setCheck('#chk-sig',sigOk===null?'na':sigOk);
  $('#v-digest').textContent = short(dig);
  const ok = digOk && sigOk===true;
  const badge=$('#vbadge');
  badge.className = 'badge '+(sigOk===null?'idle':(ok?'ok':'bad'));
  $('#vbadge-t').textContent = sigOk===null?'run verify.py':(ok?'VERIFIED':'NOT VERIFIED');
}
// ---- tamper controls: precomputed byte-exact variants, all real crypto
const seg=$('#score-seg'), orig=D.tamper.orig;
$('#tamper-desc').textContent = D.tamper.judgeName+"'s Quality score on "+D.tamper.projectTitle+" (published value "+orig+")";
const markSeg = v=>[...seg.children].forEach(x=>x.classList.toggle('on',+x.dataset.v===v));
for(let v=1;v<=5;v++){const btn=document.createElement('button');btn.textContent=v;btn.dataset.v=v;
  if(v===orig)btn.classList.add('orig');
  btn.addEventListener('click',()=>{markSeg(v);runVerify(D.scoreVariants[String(v)]);});
  seg.appendChild(btn);}
$('#btn-rankswap').addEventListener('click',()=>{markSeg(-1);runVerify(D.rankSwap);});
$('#btn-reset').addEventListener('click',()=>{markSeg(orig);runVerify(D.canonOriginal);});
markSeg(orig); runVerify(D.canonOriginal);   // start from the untouched, signed result
// APPEND-2
// ---- recompute the ranking from the raw scores (the additive model, ported)
function recompute(){
  const w=D.weights, mean=a=>a.length?a.reduce((s,v)=>s+v,0)/a.length:0;
  const recs=pl.raw_scores.map(s=>{let num=0,den=0;for(const c in s.criteria){const wc=w[c]||0;num+=wc*s.criteria[c];den+=wc;}return [s.judge,s.project,den?num/den:NaN];});
  const byJudge={},byProj={};
  recs.forEach(([j,p,x])=>{(byJudge[j]=byJudge[j]||[]).push([p,x]);(byProj[p]=byProj[p]||[]).push([j,x]);});
  let mu=mean(recs.map(r=>r[2]));const bias={},quality={};
  Object.keys(byJudge).forEach(j=>bias[j]=0);Object.keys(byProj).forEach(p=>quality[p]=0);
  for(let it=0;it<200;it++){
    for(const j in byJudge)bias[j]=mean(byJudge[j].map(([p,x])=>x-mu-quality[p]));
    let delta=0;
    for(const p in byProj){const nv=mean(byProj[p].map(([j,x])=>x-mu-bias[j]));delta=Math.max(delta,Math.abs(nv-quality[p]));quality[p]=nv;}
    if(delta<1e-10)break;
  }
  const resid=recs.map(([j,p,x])=>x-mu-bias[j]-quality[p]);
  const sigma2=mean(resid.map(r=>r*r))||1e-9;
  const eff=Object.values(quality),me=mean(eff);
  const pvar=mean(eff.map(e=>(e-me)*(e-me))), meanN=mean(Object.values(byProj).map(a=>a.length))||1;
  const k=sigma2/Math.max(pvar-sigma2/meanN,1e-6);
  const finals={};for(const p in byProj){const n=byProj[p].length,raw=mean(byProj[p].map(([j,x])=>x-mu-bias[j]));finals[p]=mu+(n/(n+k))*raw;}
  const order=Object.keys(finals).sort((a,b)=>finals[b]-finals[a]||(a<b?-1:1));
  return {order,k};
}
$('#btn-recompute').addEventListener('click',()=>{
  const r=recompute(), match=r.order[0]===D.ranking[0].project;
  const top=r.order.slice(0,3).map((p,i)=>'#'+(i+1)+' '+esc((D.ranking.find(x=>x.project===p)||{}).title||p)+' ('+p+')');
  $('#rc-out').innerHTML='<div class="check '+(match?'pass':'fail')+'"><span class="m">'+(match?'✓':'✗')+'</span><span>Recomputed winner is '+esc(D.ranking[0].title)+', it matches published rank 1. Fitted k = '+r.k.toFixed(3)+'</span></div><div class="band" style="margin-top:8px">Top 3 recomputed: '+top.join(' · ')+'</div>';
});
$('#btn-download').addEventListener('click',()=>{
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(D.bundle,null,2)],{type:'application/json'}));
  a.download='bundle.json';a.click();
});
// ---- real-data renders: gallery, judge view, results
const winId=D.ranking[0].project, gg=$('#gal-grid');
D.gallery.forEach(p=>{const d=document.createElement('div');d.className='proj'+(p.id===winId?' win':'');
  d.innerHTML=(p.id===winId?'<span class="crown">WINNER</span>':'')+'<div class="tk">'+esc(p.track)+'</div><h4>'+esc(p.title)+'</h4><div class="team">'+esc(p.team)+'</div>';gg.appendChild(d);});
$('#jv-name').textContent=D.judgeView.judgeName;
let jh='<thead><tr><th>Project</th><th class="num">Func</th><th class="num">Qual</th><th class="num">Innov</th></tr></thead><tbody>';
D.judgeView.scores.forEach(([pid,title,c])=>{jh+='<tr><td>'+esc(title)+'</td><td class="num">'+(c.functionality??'')+'</td><td class="num">'+(c.quality??'')+'</td><td class="num">'+(c.innovation??'')+'</td></tr>';});
$('#jv-table').innerHTML=jh+'</tbody>';
let rh='<thead><tr><th class="num">#</th><th>Project</th><th>Team</th><th class="num">Final</th><th class="num">n</th><th class="num">Band</th></tr></thead><tbody>';
D.ranking.forEach(r=>{const g=D.gallery.find(x=>x.id===r.project)||{};rh+='<tr class="'+(r.rank===1?'win':'')+'"><td class="num">'+r.rank+'</td><td>'+esc(r.title)+'</td><td>'+esc(g.team||'')+'</td><td class="num">'+r.final.toFixed(3)+'</td><td class="num">'+r.n+'</td><td class="num">±'+r.uncertainty.toFixed(3)+'</td></tr>';});
$('#res-table').innerHTML=rh+'</tbody>';
// ---- copy buttons on command bars
function fallbackCopy(text){const ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');}catch(e){}document.body.removeChild(ta);}
document.querySelectorAll('.cmd button[data-copy]').forEach(btn=>{
  btn.addEventListener('click',()=>{
    const text=btn.parentElement.querySelector('code').innerText;
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).catch(()=>fallbackCopy(text));}
    else{fallbackCopy(text);}
    const label=btn.dataset.label||(btn.dataset.label=btn.textContent);
    btn.classList.add('copied');btn.textContent='Copied';
    setTimeout(()=>{btn.classList.remove('copied');btn.textContent=label;},1200);
  });
});
})();
