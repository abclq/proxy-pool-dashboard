// Proxy Pool Dashboard — v11
const STATE = {page:1, pages:1, perPage:50, sort:'delay', asc:true, filters:{grade:'',country:'',protocol:'',delay:'',search:''}};
let statsCache = null, abortCtrl = null;
const MAX_PAGE = 7;

const COUNTRY_NAME = {
CN:"中国",HK:"香港",TW:"台湾",MO:"澳门",US:"美国",JP:"日本",KR:"韩国",SG:"新加坡",
DE:"德国",GB:"英国",FR:"法国",IT:"意大利",ES:"西班牙",NL:"荷兰",SE:"瑞典",CH:"瑞士",
CA:"加拿大",AU:"澳大利亚",NZ:"新西兰",VN:"越南",TH:"泰国",MY:"马来西亚",ID:"印度尼西亚",PH:"菲律宾",
IN:"印度",PK:"巴基斯坦",BD:"孟加拉国",RU:"俄罗斯",UA:"乌克兰",PL:"波兰",CZ:"捷克",
BR:"巴西",AR:"阿根廷",MX:"墨西哥",CL:"智利",ZA:"南非",EG:"埃及",NG:"尼日利亚",KE:"肯尼亚",
SA:"沙特阿拉伯",AE:"阿联酋",TR:"土耳其",IL:"以色列",FI:"芬兰",NO:"挪威",DK:"丹麦",IE:"爱尔兰",AT:"奥地利",BE:"比利时",
CO:"哥伦比亚",IR:"伊朗",KH:"柬埔寨",EC:"厄瓜多尔",RO:"罗马尼亚",KZ:"哈萨克斯坦",PE:"秘鲁",
BZ:"伯利兹",CW:"库拉索",EE:"爱沙尼亚",LT:"立陶宛",VE:"委内瑞拉",BG:"保加利亚",
BO:"玻利维亚",SY:"叙利亚",VG:"英属维京",HN:"洪都拉斯",PY:"巴拉圭",IQ:"伊拉克",
RS:"塞尔维亚",ZW:"津巴布韦",GT:"危地马拉",DO:"多米尼加",SC:"塞舌尔",CR:"哥斯达黎加",
IM:"马恩岛",PA:"巴拿马",MN:"蒙古",LV:"拉脱维亚",CY:"塞浦路斯",
LY:"利比亚",NP:"尼泊尔",OM:"阿曼",HU:"匈牙利",
NEPAL:"尼泊尔",HUNGARY:"匈牙利",
};

function esc(s){if(s==null)return'';const d=document.createElement('div');d.appendChild(document.createTextNode(String(s)));return d.innerHTML;}
function escAttr(s){if(s==null)return'';return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

async function api(path,signal){const r=await fetch(path,{signal});if(!r.ok)throw new Error(r.status);return r.json();}

async function loadStats(){
  try{statsCache=await api('/api/stats');renderStats();buildCountryDropdown();}catch(e){console.error('stats',e);}
}

function renderStats(){
  if(!statsCache)return;
  const{total,grades,china}=statsCache;
  document.getElementById('v-total').textContent=total.toLocaleString();
  document.getElementById('v-s').textContent=(grades.s||0).toLocaleString();
  document.getElementById('v-a').textContent=(grades.a||0).toLocaleString();
  document.getElementById('v-b').textContent=(grades.b||0).toLocaleString();
  document.getElementById('v-c').textContent=(grades.c||0).toLocaleString();
  document.getElementById('v-cn').textContent=(china||0).toLocaleString();
}

function buildCountryDropdown(){
  const sel=document.querySelector('[name=country]');
  if(!sel||!statsCache||!statsCache.regions)return;
  const prev=sel.value;
  while(sel.options.length>2)sel.remove(2);
  const regions=Object.entries(statsCache.regions).sort((a,b)=>b[1]-a[1]);
  for(const[code,_n]of regions){
    if(!code||code==='?')continue;
    const name=COUNTRY_NAME[code]||code;
    const opt=document.createElement('option');
    opt.value=code;
    opt.textContent=`${name} (${code})`;
    sel.appendChild(opt);
  }
  if(prev&&[...sel.options].some(o=>o.value===prev))sel.value=prev;
}

function readFilters(){
  STATE.filters.grade=document.querySelector('[name=grade]')?.value||'';
  STATE.filters.country=document.querySelector('[name=country]')?.value||'';
  STATE.filters.protocol=document.querySelector('[name=protocol]')?.value||'';
  STATE.filters.delay=document.querySelector('[name=delay]')?.value||'';
  STATE.filters.search=(document.querySelector('[name=search]')?.value||'').trim().toLowerCase();
}

function buildURL(){
  const p=new URLSearchParams();
  if(STATE.filters.grade)p.set('grade',STATE.filters.grade);
  if(STATE.filters.country)p.set('country',STATE.filters.country);
  if(STATE.filters.protocol)p.set('protocol',STATE.filters.protocol);
  if(STATE.filters.delay)p.set('delay',STATE.filters.delay);
  if(STATE.filters.search)p.set('search',STATE.filters.search);
  p.set('sort',STATE.sort);
  p.set('asc',STATE.asc?'1':'0');
  p.set('page',STATE.page);
  p.set('limit',STATE.perPage);
  return '/api/proxies?'+p.toString();
}

async function loadData(retries=2){
  if(abortCtrl)abortCtrl.abort();
  abortCtrl=new AbortController();
  const tbody=document.querySelector('tbody');
  tbody.classList.add('loading');
  for(let i=0;i<=retries;i++){
    try{
      const data=await api(buildURL(),abortCtrl.signal);
      STATE.pages=data.pages||1;
      STATE.page=data.page||1;
      renderTable(data.proxies||[]);
      renderPagination();
      buildProtocolDropdown(STATE.filters.country);
      tbody.classList.remove('loading');
      return;
    }catch(e){
      if(e.name==='AbortError'){tbody.classList.remove('loading');return;}
      if(i===retries){
        tbody.classList.remove('loading');
        document.querySelector('tbody').innerHTML='<tr><td colspan="10">加载失败，<a href="#" onclick="loadData();return false">重试</a></td></tr>';
      }else{await new Promise(r=>setTimeout(r,1000));}
    }
  }
}

function buildProtocolDropdown(country){
  if(window._protoAbort)window._protoAbort.abort();
  window._protoAbort=new AbortController();
  const sel=document.querySelector('[name=protocol]');
  if(!sel||!statsCache)return;
  const prev=sel.value;
  while(sel.options.length>1)sel.remove(1);
  let protoSet=new Set(['http','https','socks4','socks5']);
  if(country){
    const q=new URLSearchParams({country:country,limit:'200'});
    api('/api/proxies?'+q.toString(),window._protoAbort.signal).then(r=>{
      const protocols=new Set();
      (r.proxies||[]).forEach(p=>protocols.add(p.protocol||'?'));
      protocols.forEach(p=>protoSet.add(p));
      while(sel.options.length>1)sel.remove(1);
      protoSet.forEach(p=>{
        const opt=document.createElement('option');
        opt.value=p;
        opt.textContent=p==='http'?'HTTP':p==='https'?'HTTPS':p==='socks5'?'SOCKS5':'SOCKS4';
        sel.appendChild(opt);
      });
      sel.value=prev;
    }).catch(e=>{if(e.name!=='AbortError')console.error('proto',e);});
  }else{
    allProtos.forEach(([code,count])=>{
      if(code==='?')return;
      const opt=document.createElement('option');
      opt.value=code;
      opt.textContent=code==='http'?'HTTP':code==='https'?'HTTPS':code==='socks5'?'SOCKS5':'SOCKS4'+' ('+count+')';
      sel.appendChild(opt);
    });
    sel.value=prev;
  }
}

function renderTable(proxies){
  const tbody=document.querySelector('tbody');
  if(!proxies.length){tbody.innerHTML='<tr><td colspan="10">无数据</td></tr>';return;}
  const gradeLabel={s:'S',a:'A',b:'B',c:'C'};
  let html='';
  for(const p of proxies){
    let regionDisp=COUNTRY_NAME[p.region]||p.region||'?';
    if(p.region==='CN'&&p.location&&p.location!=='?'){
      regionDisp=COUNTRY_NAME.CN+'·'+p.location;
    }else if(p.location&&p.location!=='?'){
      regionDisp=COUNTRY_NAME[p.region]||p.location||p.region||'?';
    }
    html+='<tr>'+
      `<td>${esc(p.ip)} <button class="copy-btn" data-copy="${escAttr(p.ip+':'+p.port)}" title="复制">📋</button></td>`+
      `<td>${esc(String(p.port))}</td>`+
      `<td>${esc(p.protocol||'?')}</td>`+
      `<td><span class="badge ${escAttr(p.grade||'c')}">${gradeLabel[p.grade]||'?'}</span></td>`+
      `<td>${esc((p.delay||0).toFixed(0))}ms</td>`+
      `<td>${esc(regionDisp)}</td>`+
      `<td>${esc(p.region||'?')}</td>`+
      `<td>${esc(p.source||'?')}</td>`+
      `<td>${esc(p.anon||'?')}</td>`+
      `<td>${esc(String(p.last_check||'?').slice(0,10))}</td>`+
      '</tr>';
  }
  tbody.innerHTML=html;
}

function renderPagination(){
  const nav=document.getElementById('pagination');
  if(!nav)return;
  const{page,pages}=STATE;
  if(pages<=1){nav.innerHTML='';return;}
  let html='';
  let start=Math.max(1,page-Math.floor(MAX_PAGE/2));
  let end=Math.min(pages,start+MAX_PAGE-1);
  if(end-start+1<MAX_PAGE)start=Math.max(1,end-MAX_PAGE+1);
  if(page>1)html+=`<a href="#" data-page="1">‹</a><a href="#" data-page="${page-1}">‹</a>`;
  for(let i=start;i<=end;i++){
    if(i===page)html+=`<span class="cur">${i}</span>`;
    else html+=`<a href="#" data-page="${i}">${i}</a>`;
  }
  if(page<pages)html+=`<a href="#" data-page="${page+1}">›</a><a href="#" data-page="${pages}">»</a>`;
  nav.innerHTML=html;
}

function onFilterChange(){
  readFilters();
  STATE.page=1;
  loadData();
}

function onSort(col){
  if(STATE.sort===col)STATE.asc=!STATE.asc;
  else{STATE.sort=col;STATE.asc=true;}
  loadData();
}

function onPageClick(ev){
  const el=ev.target.closest('a[data-page],button[data-page]');
  if(!el)return;
  ev.preventDefault();
  STATE.page=parseInt(el.dataset.page);
  loadData();
}

function onCopy(ev){
  const btn=ev.target.closest('.copy-btn');
  if(!btn)return;
  const text=btn.dataset.copy;
  navigator.clipboard.writeText(text).then(()=>{
    const toast=document.getElementById('toast')||(()=>{const t=document.createElement('div');t.id='toast';document.body.appendChild(t);return t;})();
    toast.textContent='已复制 '+text;
    toast.classList.add('show');
    setTimeout(()=>toast.classList.remove('show'),1500);
  }).catch(()=>{
    const ta=document.createElement('textarea');
    ta.value=text;ta.style.position='fixed';ta.style.opacity=0;
    document.body.appendChild(ta);ta.select();
    document.execCommand('copy');document.body.removeChild(ta);
  });
}

function onKeydown(ev){
  const tag=ev.target.tagName;
  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT')return;
  if(ev.key==='ArrowLeft'){if(STATE.page>1){STATE.page--;loadData();}}
  if(ev.key==='ArrowRight'){if(STATE.page<STATE.pages){STATE.page++;loadData();}}
}

window.addEventListener('DOMContentLoaded',()=>{
  const countrySel=document.querySelector('[name=country]');
  const protocolSel=document.querySelector('[name=protocol]');
  countrySel?.addEventListener('change',()=>{
    readFilters();
    STATE.page=1;
    buildProtocolDropdown(STATE.filters.country);
    loadData();
  });
  protocolSel?.addEventListener('change',onFilterChange);
  document.querySelectorAll('[name=grade]').forEach(el=>el.addEventListener('change',onFilterChange));
  let delayTimer;
  document.querySelector('[name=delay]')?.addEventListener('input',ev=>{
    clearTimeout(delayTimer);
    delayTimer=setTimeout(onFilterChange,300);
  });
  let searchTimer;
  document.querySelector('[name=search]')?.addEventListener('input',ev=>{
    clearTimeout(searchTimer);
    searchTimer=setTimeout(onFilterChange,300);
  });
  document.querySelectorAll('thead th[data-sort]').forEach(th=>th.addEventListener('click',()=>onSort(th.dataset.sort)));
  document.getElementById('pagination')?.addEventListener('click',onPageClick);
  document.querySelector('tbody')?.addEventListener('click',onCopy);
  document.addEventListener('keydown',onKeydown);
  loadStats();
  loadData();
});