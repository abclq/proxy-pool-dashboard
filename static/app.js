1|// Proxy Pool Dashboard — v11
2|const STATE = {page:1, pages:1, perPage:50, sort:'delay', asc:true, filters:{grade:'',country:'',protocol:'',delay:'',search:''}};
3|let statsCache = null;
4|const MAX_PAGE = 7;
5|
6|const COUNTRY_NAME = {
7|CN:"中国",HK:"香港",TW:"台湾",MO:"澳门",US:"美国",JP:"日本",KR:"韩国",SG:"新加坡",
8|DE:"德国",GB:"英国",FR:"法国",IT:"意大利",ES:"西班牙",NL:"荷兰",SE:"瑞典",CH:"瑞士",
9|CA:"加拿大",AU:"澳大利亚",NZ:"新西兰",VN:"越南",TH:"泰国",MY:"马来西亚",ID:"印度尼西亚",PH:"菲律宾",
10|IN:"印度",PK:"巴基斯坦",BD:"孟加拉国",RU:"俄罗斯",UA:"乌克兰",PL:"波兰",CZ:"捷克",
11|BR:"巴西",AR:"阿根廷",MX:"墨西哥",CL:"智利",ZA:"南非",EG:"埃及",NG:"尼日利亚",KE:"肯尼亚",
12|SA:"沙特阿拉伯",AE:"阿联酋",TR:"土耳其",IL:"以色列",FI:"芬兰",NO:"挪威",DK:"丹麦",IE:"爱尔兰",AT:"奥地利",BE:"比利时",
13|CO:"哥伦比亚",IR:"伊朗",KH:"柬埔寨",EC:"厄瓜多尔",RO:"罗马尼亚",KZ:"哈萨克斯坦",PE:"秘鲁",
14|BZ:"伯利兹",CW:"库拉索",EE:"爱沙尼亚",LT:"立陶宛",VE:"委内瑞拉",BG:"保加利亚",
15|BO:"玻利维亚",SY:"叙利亚",VG:"英属维京",HN:"洪都拉斯",PY:"巴拉圭",IQ:"伊拉克",
16|RS:"塞尔维亚",ZW:"津巴布韦",GT:"危地马拉",DO:"多米尼加",SC:"塞舌尔",CR:"哥斯达黎加",
17|IM:"马恩岛",PA:"巴拿马",MN:"蒙古",LV:"拉脱维亚",CY:"塞浦路斯",
18|LY:"利比亚",NP:"尼泊尔",OM:"阿曼",HU:"匈牙利",
19|NEPAL:"尼泊尔",HUNGARY:"匈牙利",
20|};
21|
22|function esc(s){if(s==null)return'';const d=document.createElement('div');d.appendChild(document.createTextNode(String(s)));return d.innerHTML;}
23|function escAttr(s){if(s==null)return'';return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
24|
25|async function api(path,signal){const r=await fetch(path,{signal});if(!r.ok)throw new Error(r.status);return r.json();}
26|
27|async function loadStats(){
28|  try{statsCache=await api('/api/stats');renderStats();buildCountryDropdown();renderCountryGrid();}catch(e){console.error('stats',e);}
29|}
30|
31|function renderStats(){
32|  if(!statsCache)return;
33|  const{total,grades,china}=statsCache;
34|  document.getElementById('v-total').textContent=total.toLocaleString();
35|  document.getElementById('v-s').textContent=(grades.s||0).toLocaleString();
36|  document.getElementById('v-a').textContent=(grades.a||0).toLocaleString();
37|  document.getElementById('v-b').textContent=(grades.b||0).toLocaleString();
38|  document.getElementById('v-c').textContent=(grades.c||0).toLocaleString();
39|  document.getElementById('v-cn').textContent=(china||0).toLocaleString();
40|}
41|
42|function buildCountryDropdown(){
43|  const sel=document.querySelector('[name=country]');
44|  if(!sel||!statsCache||!statsCache.regions)return;
45|  const prev=sel.value;
46|  while(sel.options.length>2)sel.remove(2);
47|  const regions=Object.entries(statsCache.regions).sort((a,b)=>b[1]-a[1]);
48|  for(const[code,_n]of regions){
49|    if(!code||code==='?')continue;
50|    const name=COUNTRY_NAME[code]||code;
51|    const opt=document.createElement('option');
52|    opt.value=code;
53|    opt.textContent=`${name} (${code})`;
54|    sel.appendChild(opt);
55|  }
56|  if(prev&&[...sel.options].some(o=>o.value===prev))sel.value=prev;
57|}
58|
59|function renderCountryGrid(){
60|  if(!statsCache||!statsCache.regions)return;
61|  const grid=document.getElementById('country-grid');
62|  if(!grid)return;
63|  const entries=Object.entries(statsCache.regions)
64|    .filter(([code])=>code&&code!=='?')
65|    .sort((a,b)=>b[1]-a[1]);
66|  let html='';
67|  for(const[code,count]of entries){
68|    const name=COUNTRY_NAME[code]||code;
69|    html+=`<div class="country-card" onclick="filterCountry('${escAttr(code)}')"><div class="cc">${count.toLocaleString()}</div><div class="cn">${esc(name)}</div></div>`;
70|  }
71|  grid.innerHTML=html;
72|}
73|
74|function filterCountry(code){
75|  const sel=document.querySelector('[name=country]');
76|  if(sel)sel.value=code;
77|  readFilters();
78|  STATE.page=1;
79|  loadData();
80|}
81|
82|function showCountryGrid(){
83|  document.getElementById('country-grid').style.display='';
84|  document.getElementById('table-area').style.display='none';
85|  const sel=document.querySelector('[name=country]');
86|  if(sel)sel.value='';
87|  readFilters();
88|}
89|
90|function readFilters(){
91|  STATE.filters.grade=document.querySelector('[name=grade]')?.value||'';
92|  STATE.filters.country=document.querySelector('[name=country]')?.value||'';
93|  STATE.filters.protocol=document.querySelector('[name=protocol]')?.value||'';
94|  STATE.filters.delay=document.querySelector('[name=delay]')?.value||'';
95|  STATE.filters.search=(document.querySelector('[name=search]')?.value||'').trim().toLowerCase();
96|}
97|
98|function buildURL(){
99|  const p=new URLSearchParams();
100|  if(STATE.filters.grade)p.set('grade',STATE.filters.grade);
101|  if(STATE.filters.country)p.set('country',STATE.filters.country);
102|  if(STATE.filters.protocol)p.set('protocol',STATE.filters.protocol);
103|  if(STATE.filters.delay)p.set('delay',STATE.filters.delay);
104|  if(STATE.filters.search)p.set('search',STATE.filters.search);
105|  p.set('sort',STATE.sort);
106|  p.set('asc',STATE.asc?'1':'0');
107|  p.set('page',STATE.page);
108|  p.set('limit',STATE.perPage);
109|  return '/api/proxies?'+p.toString();
110|}
111|
112|async function loadData(){
113|  document.getElementById('country-grid').style.display='none';
114|  document.getElementById('table-area').style.display='';
115|  const tbody=document.querySelector('tbody');
116|  tbody.classList.add('loading');
117|  try{
118|    const data=await api(buildURL());
119|    STATE.pages=data.pages||1;
120|    STATE.page=data.page||1;
121|    renderTable(data.proxies||[]);
122|    renderPagination();
123|    buildProtocolDropdown(STATE.filters.country);
124|  }catch(e){
125|    document.querySelector('tbody').innerHTML='<tr><td colspan="10">加载失败，<a href="#" onclick="loadData();return false">重试</a></td></tr>';
126|    document.getElementById('pagination').innerHTML='';
127|  }
128|  tbody.classList.remove('loading');
129|}
130|
131|function buildProtocolDropdown(country){
132|  if(window._protoAbort)window._protoAbort.abort();
133|  window._protoAbort=new AbortController();
134|  const sel=document.querySelector('[name=protocol]');
135|  if(!sel||!statsCache)return;
136|  const prev=sel.value;
137|  while(sel.options.length>1)sel.remove(1);
138|  let protoSet=new Set(['http','https','socks4','socks5']);
139|  if(country){
140|    const q=new URLSearchParams({country:country,limit:'200'});
141|    api('/api/proxies?'+q.toString(),window._protoAbort.signal).then(r=>{
142|      const protocols=new Set();
143|      (r.proxies||[]).forEach(p=>protocols.add(p.protocol||'?'));
144|      protocols.forEach(p=>protoSet.add(p));
145|      while(sel.options.length>1)sel.remove(1);
146|      protoSet.forEach(p=>{
147|        const opt=document.createElement('option');
148|        opt.value=p;
149|        opt.textContent=p==='http'?'HTTP':p==='https'?'HTTPS':p==='socks5'?'SOCKS5':'SOCKS4';
150|        sel.appendChild(opt);
151|      });
152|      sel.value=prev;
153|    }).catch(e=>{if(e.name!=='AbortError')console.error('proto',e);});
154|  }else{
155|    if(statsCache?.protocols){
156|      Object.entries(statsCache.protocols).forEach(([code,count])=>{
157|        if(code==='?')return;
158|        const opt=document.createElement('option');
159|        opt.value=code;
160|        opt.textContent=code==='http'?'HTTP':code==='https'?'HTTPS':code==='socks5'?'SOCKS5':'SOCKS4'+' ('+count+')';
161|        sel.appendChild(opt);
162|      });
163|    }
164|    sel.value=prev;
165|  }
166|}
167|
168|function renderTable(proxies){
169|  const tbody=document.querySelector('tbody');
170|  if(!proxies.length){tbody.innerHTML='<tr><td colspan="10">无数据</td></tr>';return;}
171|  const gradeLabel={s:'S',a:'A',b:'B',c:'C'};
172|  let html='';
173|  for(const p of proxies){
174|    let regionDisp=COUNTRY_NAME[p.region]||p.region||'?';
175|    if(p.region==='CN'&&p.location&&p.location!=='?'){
176|      regionDisp=COUNTRY_NAME.CN+'·'+p.location;
177|    }else if(p.location&&p.location!=='?'){
178|      regionDisp=COUNTRY_NAME[p.region]||p.location||p.region||'?';
179|    }
180|    html+='<tr>'+
181|      `<td>${esc(p.ip)} <button class="copy-btn" data-copy="${escAttr(p.ip+':'+p.port)}" title="复制">📋</button></td>`+
182|      `<td>${esc(String(p.port))}</td>`+
183|      `<td>${esc(p.protocol||'未知')}</td>`+
184|      `<td><span class="badge ${escAttr(p.grade||'c')}">${gradeLabel[p.grade]||'未知'}</span></td>`+
185|      `<td>${esc((p.delay||0).toFixed(0))}ms</td>`+
186|      `<td>${esc(regionDisp)}</td>`+
187|      `<td>${esc(p.region||'未知')}</td>`+
188|      `<td>${esc(p.source||'未知')}</td>`+
189|      `<td>${esc(p.anon||'未知')}</td>`+
190|      `<td>${esc(String(p.last_check||'未知').slice(0,10))}</td>`+
191|      '</tr>';
192|  }
193|  tbody.innerHTML=html;
194|}
195|
196|function renderPagination(){
197|  const nav=document.getElementById('pagination');
198|  if(!nav)return;
199|  const{page,pages}=STATE;
200|  if(pages<=1){nav.innerHTML='';return;}
201|  let html='';
202|  let start=Math.max(1,page-Math.floor(MAX_PAGE/2));
203|  let end=Math.min(pages,start+MAX_PAGE-1);
204|  if(end-start+1<MAX_PAGE)start=Math.max(1,end-MAX_PAGE+1);
205|  if(page>1)html+=`<a href="#" data-page="1">‹</a><a href="#" data-page="${page-1}">‹</a>`;
206|  for(let i=start;i<=end;i++){
207|    if(i===page)html+=`<span class="cur">${i}</span>`;
208|    else html+=`<a href="#" data-page="${i}">${i}</a>`;
209|  }
210|  if(page<pages)html+=`<a href="#" data-page="${page+1}">›</a><a href="#" data-page="${pages}">»</a>`;
211|  nav.innerHTML=html;
212|}
213|
214|function onFilterChange(){
215|  readFilters();
216|  STATE.page=1;
217|  loadData();
218|}
219|
220|function onSort(col){
221|  if(STATE.sort===col)STATE.asc=!STATE.asc;
222|  else{STATE.sort=col;STATE.asc=true;}
223|  loadData();
224|}
225|
226|function onPageClick(ev){
227|  const el=ev.target.closest('a[data-page],button[data-page]');
228|  if(!el)return;
229|  ev.preventDefault();
230|  STATE.page=parseInt(el.dataset.page);
231|  loadData();
232|}
233|
234|function onCopy(ev){
235|  const btn=ev.target.closest('.copy-btn');
236|  if(!btn)return;
237|  const text=btn.dataset.copy;
238|  navigator.clipboard.writeText(text).then(()=>{
239|    const toast=document.getElementById('toast')||(()=>{const t=document.createElement('div');t.id='toast';document.body.appendChild(t);return t;})();
240|    toast.textContent='已复制 '+text;
241|    toast.classList.add('show');
242|    setTimeout(()=>toast.classList.remove('show'),1500);
243|  }).catch(()=>{
244|    const ta=document.createElement('textarea');
245|    ta.value=text;ta.style.position='fixed';ta.style.opacity=0;
246|    document.body.appendChild(ta);ta.select();
247|    document.execCommand('copy');document.body.removeChild(ta);
248|  });
249|}
250|
251|function onKeydown(ev){
252|  const tag=ev.target.tagName;
253|  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT')return;
254|  if(ev.key==='ArrowLeft'){if(STATE.page>1){STATE.page--;loadData();}}
255|  if(ev.key==='ArrowRight'){if(STATE.page<STATE.pages){STATE.page++;loadData();}}
256|}
257|
258|function init(){
259|  if(STATE._ready)return;STATE._ready=1;
260|  const countrySel=document.querySelector('[name=country]');
261|  const protocolSel=document.querySelector('[name=protocol]');
262|  countrySel?.addEventListener('change',()=>{
263|    readFilters();
264|    STATE.page=1;
265|    buildProtocolDropdown(STATE.filters.country);
266|    loadData();
267|  });
268|  protocolSel?.addEventListener('change',onFilterChange);
269|  document.querySelectorAll('[name=grade]').forEach(el=>el.addEventListener('change',onFilterChange));
270|  let delayTimer;
271|  document.querySelector('[name=delay]')?.addEventListener('input',ev=>{
272|    clearTimeout(delayTimer);
273|    delayTimer=setTimeout(onFilterChange,300);
274|  });
275|  let searchTimer;
276|  document.querySelector('[name=search]')?.addEventListener('input',ev=>{
277|    clearTimeout(searchTimer);
278|    searchTimer=setTimeout(onFilterChange,300);
279|  });
280|  document.querySelectorAll('thead th[data-sort]').forEach(th=>th.addEventListener('click',()=>onSort(th.dataset.sort)));
281|  document.getElementById('pagination')?.addEventListener('click',onPageClick);
282|  document.querySelector('tbody')?.addEventListener('click',onCopy);
283|  document.addEventListener('keydown',onKeydown);
284|  loadStats();
285|}
286|if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();