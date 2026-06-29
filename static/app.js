// Proxy Pool Dashboard — v12 (country overview → drill-down)
const STATE = {page:1, pages:1, perPage:50, sort:'delay', asc:true, selectedCountry:null, countries:[]};
let statsCache = null;
let abortCtrl = null;
const MAX_PAGE = 7;

const COUNTRY_FLAG = {
  CN:'🇨🇳', HK:'🇭🇰', TW:'🇹🇼', MO:'🇲🇴', US:'🇺🇸', JP:'🇯🇵', KR:'🇰🇷', SG:'🇸🇬',
  DE:'🇩🇪', GB:'🇬🇧', FR:'🇫🇷', IT:'🇮🇹', ES:'🇪🇸', NL:'🇳🇱', SE:'🇸🇪', CH:'🇨🇭',
  CA:'🇨🇦', AU:'🇦🇺', NZ:'🇳🇿', VN:'🇻🇳', TH:'🇹🇭', MY:'🇲🇾', ID:'🇮🇩', PH:'🇵🇭',
  IN:'🇮🇳', PK:'🇵🇰', BD:'🇧🇩', RU:'🇷🇺', UA:'🇺🇦', PL:'🇵🇱', CZ:'🇨🇿',
  BR:'🇧🇷', AR:'🇦🇷', MX:'🇲🇽', CL:'🇨🇱', ZA:'🇿🇦', EG:'🇪🇬', NG:'🇳🇬', KE:'🇰🇪',
  SA:'🇸🇦', AE:'🇦🇪', TR:'🇹🇷', IL:'🇮🇱', FI:'🇫🇮', NO:'🇳🇴', DK:'🇩🇰', IE:'🇮🇪', AT:'🇦🇹', BE:'🇧🇪',
  CO:'🇨🇴', IR:'🇮🇷', KH:'🇰🇭', EC:'🇪🇨', RO:'🇷🇴', KZ:'🇰🇿', PE:'🇵🇪',
  BZ:'🇧🇿', EE:'🇪🇪', LT:'🇱🇹', VE:'🇻🇪', BG:'🇧🇬', SY:'🇸🇾',
  HN:'🇭🇳', PY:'🇵🇾', IQ:'🇮🇶', RS:'🇷🇸', ZW:'🇿🇼', GT:'🇬🇹', DO:'🇩🇴', SC:'🇸🇨', CR:'🇨🇷',
  PA:'🇵🇦', MN:'🇲🇳', LV:'🇱🇻', CY:'🇨🇾', LY:'🇱🇾', NP:'🇳🇵', OM:'🇴🇲', HU:'🇭🇺',
  BO:'🇧🇴', CW:'🇨🇼', VG:'🇻🇬', IM:'🇮🇲',
};

const COUNTRY_NAME = {
  CN:"中国",HK:"香港",TW:"台湾",MO:"澳门",US:"美国",JP:"日本",KR:"韩国",SG:"新加坡",
  DE:"德国",GB:"英国",FR:"法国",IT:"意大利",ES:"西班牙",NL:"荷兰",SE:"瑞典",CH:"瑞士",
  CA:"加拿大",AU:"澳大利亚",NZ:"新西兰",VN:"越南",TH:"泰国",MY:"马来西亚",ID:"印度尼西亚",PH:"菲律宾",
  IN:"印度",PK:"巴基斯坦",BD:"孟加拉国",RU:"俄罗斯",UA:"乌克兰",PL:"波兰",CZ:"捷克",
  BR:"巴西",AR:"阿根廷",MX:"墨西哥",CL:"智利",ZA:"南非",EG:"埃及",NG:"尼日利亚",KE:"肯尼亚",
  SA:"沙特阿拉伯",AE:"阿联酋",TR:"土耳其",IL:"以色列",FI:"芬兰",NO:"挪威",DK:"丹麦",IE:"爱尔兰",AT:"奥地利",BE:"比利时",
  CO:"哥伦比亚",IR:"伊朗",KH:"柬埔寨",EC:"厄瓜多尔",RO:"罗马尼亚",KZ:"哈萨克斯坦",PE:"秘鲁",
  BZ:"伯利兹",EE:"爱沙尼亚",LT:"立陶宛",VE:"委内瑞拉",BG:"保加利亚",SY:"叙利亚",
  HN:"洪都拉斯",PY:"巴拉圭",IQ:"伊拉克",RS:"塞尔维亚",ZW:"津巴布韦",GT:"危地马拉",DO:"多米尼加",SC:"塞舌尔",CR:"哥斯达黎加",
  PA:"巴拿马",MN:"蒙古",LV:"拉脱维亚",CY:"塞浦路斯",LY:"利比亚",NP:"尼泊尔",OM:"阿曼",HU:"匈牙利",
  BO:"玻利维亚",CW:"库拉索",VG:"英属维京",IM:"马恩岛",
};

function $(id){return document.getElementById(id);}
function esc(s){if(s==null)return'';const d=document.createElement('div');d.appendChild(document.createTextNode(String(s)));return d.innerHTML;}
function escAttr(s){if(s==null)return'';return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function v(s){if(s===null||s===undefined||s==='?'||s==='??'||s==='unknown'||s==='未知')return'未知';return s;}

async function api(path){
  if(abortCtrl)abortCtrl.abort();
  abortCtrl=new AbortController();
  const r=await fetch(path,{signal:abortCtrl.signal});
  if(!r.ok)throw new Error(r.status);
  return r.json();
}

// ═══════════════════ COUNTRY VIEW ═══════════════════
async function loadCountries(){
  try{
    const data=await api('/api/countries');
    STATE.countries=data.countries||[];
    statsCache={countries:STATE.countries,total:STATE.countries.reduce((s,c)=>s+c.count,0)};
    renderStats();
    renderCountryGrid();
    $('header-sub').textContent=`共 ${STATE.countries.length} 个国家/地区 · ${statsCache.total.toLocaleString()} 个代理节点`;
  }catch(e){
    console.error('countries',e);
    $('header-sub').textContent='加载失败，刷新重试';
  }
}

function renderStats(){
  if(!statsCache)return;
  $('v-total').textContent=statsCache.total.toLocaleString();
  // Estimate grades from stats endpoint (loaded in background)
  // For now just show total
  setTimeout(loadDetailStats,50);
}

async function loadDetailStats(){
  try{
    const s=await api('/api/stats');
    $('v-s').textContent=((s.grades&&s.grades.s)||0).toLocaleString();
    $('v-a').textContent=((s.grades&&s.grades.a)||0).toLocaleString();
    $('v-d').textContent=((s.grades&&s.grades.d)||0).toLocaleString();
  }catch(e){}
}

function renderCountryGrid(){
  const grid=$('country-grid');
  if(!STATE.countries.length){grid.innerHTML='<div class="empty">无数据</div>';return;}
  let html='';
  for(const c of STATE.countries){
    const flag=COUNTRY_FLAG[c.code]||'🏳️';
    html+=`<div class="country-card" onclick="drillCountry('${escAttr(c.code)}')">
      <div class="flag">${flag}</div>
      <div class="info">
        <div class="name">${esc(c.name||c.code)}</div>
        <div class="code-label">${esc(c.code)}</div>
      </div>
      <div class="count">${c.count.toLocaleString()}</div>
    </div>`;
  }
  grid.innerHTML=html;
}

// ═══════════════════ NODE DRILL-DOWN ═══════════════════
function drillCountry(code){
  STATE.selectedCountry=code;
  STATE.page=1;
  const c=STATE.countries.find(x=>x.code===code)||{};
  const flag=COUNTRY_FLAG[code]||'🏳️';
  $('node-flag').textContent=flag;
  $('node-name').textContent=c.name||code;
  $('node-count').textContent=`(${c.count?c.count.toLocaleString():0} 节点)`;

  // Set location filter placeholder
  $('f-location').placeholder=`${c.name||code} 城市筛选…`;

  showNodeView();
  loadNodes();
}

function showNodeView(){
  $('country-view').style.display='none';
  $('node-view').style.display='block';
}

function showCountryView(){
  STATE.selectedCountry=null;
  $('node-view').style.display='none';
  $('country-view').style.display='block';
  loadCountries(); // refresh
}

function reloadNodes(){
  STATE.page=1;
  loadNodes();
}

function getFilters(){
  return {
    grade: $('f-grade')?.value||'',
    protocol: $('f-protocol')?.value||'',
    delay: $('f-delay')?.value||'',
    location: ($('f-location')?.value||'').trim().toLowerCase(),
    search: ($('f-search')?.value||'').trim().toLowerCase(),
  };
}

async function loadNodes(){
  if(!STATE.selectedCountry)return;
  const f=getFilters();
  const p=new URLSearchParams();
  p.set('country',STATE.selectedCountry);
  p.set('page',STATE.page);
  p.set('limit',STATE.perPage);
  p.set('sort',STATE.sort);
  p.set('asc',STATE.asc?'1':'0');
  if(f.grade)p.set('grade',f.grade);
  if(f.protocol)p.set('protocol',f.protocol);
  if(f.delay)p.set('delay',f.delay);
  if(f.location)p.set('location',f.location);
  if(f.search)p.set('search',f.search);

  const tbody=document.querySelector('#node-view tbody');
  tbody.classList.add('loading');
  try{
    const data=await api('/api/proxies?'+p.toString());
    STATE.pages=data.pages||1;
    STATE.page=data.page||1;
    renderTable(data.proxies||[]);
    renderPagination(data);
    $('node-count').textContent=`(${(data.filtered||0).toLocaleString()} / ${data.total.toLocaleString()} 节点)`;
  }catch(e){
    if(e.name!=='AbortError'){
      tbody.innerHTML='<tr><td colspan="7">加载失败 <a href="#" onclick="reloadNodes();return false">重试</a></td></tr>';
      $('pagination').innerHTML='';
    }
  }
  tbody.classList.remove('loading');
}

function renderTable(proxies){
  const tbody=document.querySelector('#node-view tbody');
  if(!proxies.length){tbody.innerHTML='<tr><td colspan="7" class="empty">无匹配节点</td></tr>';return;}
  const gradeLabel={s:'S',a:'A',b:'B',c:'C',d:'D'};
  let html='';
  for(const p of proxies){
    const loc=v(p.location);
    const grade=v(gradeLabel[p.grade]||'?');
    html+='<tr>'+
      `<td>${esc(p.ip)}:${esc(String(p.port))} <button class="copy-btn" data-copy="${escAttr(p.ip+':'+p.port)}">📋</button></td>`+
      `<td>${esc((p.delay||0).toFixed(0))}ms</td>`+
      `<td><span class="badge ${escAttr(p.grade||'c')}">${grade}</span></td>`+
      `<td>${esc(v(p.protocol))}</td>`+
      `<td>${esc(loc)}</td>`+
      `<td>${esc(v(p.source))}</td>`+
      `<td>${esc(String(v(p.last_check)).slice(0,10))}</td>`+
      '</tr>';
  }
  tbody.innerHTML=html;
}

function renderPagination(data){
  const nav=$('pagination');
  const{page,pages}=STATE;
  if(pages<=1){nav.innerHTML='';return;}
  let html='';
  let start=Math.max(1,page-Math.floor(MAX_PAGE/2));
  let end=Math.min(pages,start+MAX_PAGE-1);
  if(end-start+1<MAX_PAGE)start=Math.max(1,end-MAX_PAGE+1);
  if(page>1)html+=`<a href="#" data-page="1">«</a><a href="#" data-page="${page-1}">‹</a>`;
  for(let i=start;i<=end;i++){
    if(i===page)html+=`<span class="cur">${i}</span>`;
    else html+=`<a href="#" data-page="${i}">${i}</a>`;
  }
  if(page<pages)html+=`<a href="#" data-page="${page+1}">›</a><a href="#" data-page="${pages}">»</a>`;
  html+=`<span class="info">共 ${data.filtered.toLocaleString()} 条</span>`;
  nav.innerHTML=html;
}

// ═══════════════════ EVENTS ═══════════════════
function onSort(col){
  if(STATE.sort===col)STATE.asc=!STATE.asc;
  else{STATE.sort=col;STATE.asc=true;}
  loadNodes();
}

function onPageClick(ev){
  const el=ev.target.closest('a[data-page],button[data-page]');
  if(!el)return;
  ev.preventDefault();
  STATE.page=parseInt(el.dataset.page);
  loadNodes();
}

function onCopy(ev){
  const btn=ev.target.closest('.copy-btn');
  if(!btn)return;
  const text=btn.dataset.copy;
  navigator.clipboard.writeText(text).then(()=>{
    const toast=$('toast');
    toast.textContent='已复制 '+text;
    toast.classList.add('show');
    setTimeout(()=>toast.classList.remove('show'),1500);
  }).catch(()=>{});
}

function onKeydown(ev){
  const tag=ev.target.tagName;
  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT')return;
  if(ev.key==='ArrowLeft'){if(STATE.page>1){STATE.page--;loadNodes();}}
  if(ev.key==='ArrowRight'){if(STATE.page<STATE.pages){STATE.page++;loadNodes();}}
}

function init(){
  if(STATE._ready)return;STATE._ready=1;

  // Sortable headers
  document.querySelectorAll('thead th[data-sort]').forEach(th=>{
    th.addEventListener('click',()=>onSort(th.dataset.sort));
  });

  // Pagination
  $('pagination')?.addEventListener('click',onPageClick);

  // Copy
  document.querySelector('#node-view tbody')?.addEventListener('click',onCopy);

  // Keyboard nav
  document.addEventListener('keydown',onKeydown);

  // Filter debounce
  let timer;
  ['f-location','f-search'].forEach(id=>{
    const el=$(id);
    if(el)el.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(reloadNodes,300);});
  });
  ['f-delay'].forEach(id=>{
    const el=$(id);
    if(el)el.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(reloadNodes,500);});
  });
  ['f-protocol','f-grade'].forEach(id=>{
    const el=$(id);
    if(el)el.addEventListener('change',reloadNodes);
  });

  // Load initial data
  loadCountries();
}

if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
