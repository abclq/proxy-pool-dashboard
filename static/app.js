1|1|// Proxy Pool Dashboard — v11
2|2|const STATE = {page:1, pages:1, perPage:50, sort:'delay', asc:true, filters:{grade:'',country:'',protocol:'',delay:'',location:'',search:''}};
3|3|let statsCache = null;
4|4|const MAX_PAGE = 7;
5|5|
6|6|const COUNTRY_NAME = {
7|7|CN:"中国",HK:"香港",TW:"台湾",MO:"澳门",US:"美国",JP:"日本",KR:"韩国",SG:"新加坡",
8|8|DE:"德国",GB:"英国",FR:"法国",IT:"意大利",ES:"西班牙",NL:"荷兰",SE:"瑞典",CH:"瑞士",
9|9|CA:"加拿大",AU:"澳大利亚",NZ:"新西兰",VN:"越南",TH:"泰国",MY:"马来西亚",ID:"印度尼西亚",PH:"菲律宾",
10|10|IN:"印度",PK:"巴基斯坦",BD:"孟加拉国",RU:"俄罗斯",UA:"乌克兰",PL:"波兰",CZ:"捷克",
11|11|BR:"巴西",AR:"阿根廷",MX:"墨西哥",CL:"智利",ZA:"南非",EG:"埃及",NG:"尼日利亚",KE:"肯尼亚",
12|12|SA:"沙特阿拉伯",AE:"阿联酋",TR:"土耳其",IL:"以色列",FI:"芬兰",NO:"挪威",DK:"丹麦",IE:"爱尔兰",AT:"奥地利",BE:"比利时",
13|13|CO:"哥伦比亚",IR:"伊朗",KH:"柬埔寨",EC:"厄瓜多尔",RO:"罗马尼亚",KZ:"哈萨克斯坦",PE:"秘鲁",
14|14|BZ:"伯利兹",CW:"库拉索",EE:"爱沙尼亚",LT:"立陶宛",VE:"委内瑞拉",BG:"保加利亚",
15|15|BO:"玻利维亚",SY:"叙利亚",VG:"英属维京",HN:"洪都拉斯",PY:"巴拉圭",IQ:"伊拉克",
16|16|RS:"塞尔维亚",ZW:"津巴布韦",GT:"危地马拉",DO:"多米尼加",SC:"塞舌尔",CR:"哥斯达黎加",
17|17|IM:"马恩岛",PA:"巴拿马",MN:"蒙古",LV:"拉脱维亚",CY:"塞浦路斯",
18|18|LY:"利比亚",NP:"尼泊尔",OM:"阿曼",HU:"匈牙利",
19|19|NEPAL:"尼泊尔",HUNGARY:"匈牙利",
20|20|};
21|21|
22|22|function esc(s){if(s==null)return'';const d=document.createElement('div');d.appendChild(document.createTextNode(String(s)));return d.innerHTML;}
23|23|function escAttr(s){if(s==null)return'';return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function v(s){if(s===null||s===undefined||s==='?'||s==='??'||s==='unknown'||s==='未知')return'未知';return s;}
24|24|
25|25|async function api(path,signal){const r=await fetch(path,{signal});if(!r.ok)throw new Error(r.status);return r.json();}
26|26|
27|27|async function loadStats(){
28|28|  try{statsCache=await api('/api/stats');renderStats();buildCountryDropdown();renderCountryGrid();}catch(e){console.error('stats',e);}
29|29|}
30|30|
31|31|function renderStats(){
32|32|  if(!statsCache)return;
33|33|  const{total,grades,china}=statsCache;
34|34|  document.getElementById('v-total').textContent=total.toLocaleString();
35|35|  document.getElementById('v-s').textContent=(grades.s||0).toLocaleString();
36|36|  document.getElementById('v-a').textContent=(grades.a||0).toLocaleString();
37|37|  document.getElementById('v-b').textContent=(grades.b||0).toLocaleString();
38|38|  document.getElementById('v-c').textContent=(grades.c||0).toLocaleString();
39|38|  document.getElementById('v-d').textContent=(grades.d||0).toLocaleString();
40|39|  document.getElementById('v-cn').textContent=(china||0).toLocaleString();
41|40|}
42|41|
43|42|function buildCountryDropdown(){
44|43|  const sel=document.querySelector('[name=country]');
45|44|  if(!sel||!statsCache||!statsCache.regions)return;
46|45|  const prev=sel.value;
47|46|  while(sel.options.length>2)sel.remove(2);
48|47|  const regions=Object.entries(statsCache.regions).sort((a,b)=>b[1]-a[1]);
49|48|  for(const[code,_n]of regions){
50|49|    if(!code||code==='?')continue;
51|50|    const name=COUNTRY_NAME[code]||code;
52|51|    const opt=document.createElement('option');
53|52|    opt.value=code;
54|53|    opt.textContent=`${name} (${code})`;
55|54|    sel.appendChild(opt);
56|55|  }
57|56|  if(prev&&[...sel.options].some(o=>o.value===prev))sel.value=prev;
58|57|}
59|58|
60|59|function renderCountryGrid(){
61|60|  if(!statsCache||!statsCache.regions)return;
62|61|  const grid=document.getElementById('country-grid');
63|62|  if(!grid)return;
64|63|  const entries=Object.entries(statsCache.regions)
65|64|    .filter(([code])=>code&&code!=='?')
66|65|    .sort((a,b)=>b[1]-a[1]);
67|66|  let html='';
68|67|  for(const[code,count]of entries){
69|68|    const name=COUNTRY_NAME[code]||code;
70|69|    html+=`<div class="country-card" onclick="filterCountry('${escAttr(code)}')"><div class="cc">${count.toLocaleString()}</div><div class="cn">${esc(name)}</div></div>`;
71|70|  }
72|71|  grid.innerHTML=html;
73|72|}
74|73|
75|74|function filterCountry(code){
76|75|  const sel=document.querySelector('[name=country]');
77|76|  if(sel)sel.value=code;
78|77|  readFilters();
79|78|  STATE.page=1;
80|79|  loadData();
81|80|}
82|81|
83|82|function showCountryGrid(){
84|83|  document.getElementById('country-grid').style.display='';
85|84|  document.getElementById('table-area').style.display='none';
86|85|  const sel=document.querySelector('[name=country]');
87|86|  if(sel)sel.value='';
88|87|  readFilters();
89|88|}
90|89|
91|90|function readFilters(){
92|91|  STATE.filters.grade=document.querySelector('[name=grade]')?.value||'';
93|92|  STATE.filters.country=document.querySelector('[name=country]')?.value||'';
94|93|  STATE.filters.protocol=document.querySelector('[name=protocol]')?.value||'';
95|94|  STATE.filters.delay=document.querySelector('[name=delay]')?.value||'';
96|94|  STATE.filters.location=(document.querySelector('[name=location]')?.value||'').trim().toLowerCase();
97|95|  STATE.filters.search=(document.querySelector('[name=search]')?.value||'').trim().toLowerCase();
98|96|}
99|97|
100|98|function buildURL(){
101|99|  const p=new URLSearchParams();
102|100|  if(STATE.filters.grade)p.set('grade',STATE.filters.grade);
103|101|  if(STATE.filters.country)p.set('country',STATE.filters.country);
104|102|  if(STATE.filters.protocol)p.set('protocol',STATE.filters.protocol);
105|103|  if(STATE.filters.delay)p.set('delay',STATE.filters.delay);
106|if(STATE.filters.location)p.set('location',STATE.filters.location);
107|104|  if(STATE.filters.search)p.set('search',STATE.filters.search);
108|105|  p.set('sort',STATE.sort);
109|106|  p.set('asc',STATE.asc?'1':'0');
110|107|  p.set('page',STATE.page);
111|108|  p.set('limit',STATE.perPage);
112|109|  return '/api/proxies?'+p.toString();
113|110|}
114|111|
115|112|async function loadData(){
116|113|  document.getElementById('country-grid').style.display='none';
117|114|  document.getElementById('table-area').style.display='';
118|115|  const tbody=document.querySelector('tbody');
119|116|  tbody.classList.add('loading');
120|117|  try{
121|118|    const data=await api(buildURL());
122|119|    STATE.pages=data.pages||1;
123|120|    STATE.page=data.page||1;
124|121|    renderTable(data.proxies||[]);
125|122|    renderPagination();
126|123|    buildProtocolDropdown(STATE.filters.country);
127|124|  }catch(e){
128|125|    document.querySelector('tbody').innerHTML='<tr><td colspan="10">加载失败，<a href="#" onclick="loadData();return false">重试</a></td></tr>';
129|126|    document.getElementById('pagination').innerHTML='';
130|127|  }
131|128|  tbody.classList.remove('loading');
132|129|}
133|130|
134|131|function buildProtocolDropdown(country){
135|132|  if(window._protoAbort)window._protoAbort.abort();
136|133|  window._protoAbort=new AbortController();
137|134|  const sel=document.querySelector('[name=protocol]');
138|135|  if(!sel||!statsCache)return;
139|136|  const prev=sel.value;
140|137|  while(sel.options.length>1)sel.remove(1);
141|138|  let protoSet=new Set(['http','https','socks4','socks5']);
142|139|  if(country){
143|140|    const q=new URLSearchParams({country:country,limit:'200'});
144|141|    api('/api/proxies?'+q.toString(),window._protoAbort.signal).then(r=>{
145|142|      const protocols=new Set();
146|143|      (r.proxies||[]).forEach(p=>protocols.add(p.protocol||'?'));
147|144|      protocols.forEach(p=>protoSet.add(p));
148|145|      while(sel.options.length>1)sel.remove(1);
149|146|      protoSet.forEach(p=>{
150|147|        const opt=document.createElement('option');
151|148|        opt.value=p;
152|149|        opt.textContent=p==='http'?'HTTP':p==='https'?'HTTPS':p==='socks5'?'SOCKS5':'SOCKS4';
153|150|        sel.appendChild(opt);
154|151|      });
155|152|      sel.value=prev;
156|153|    }).catch(e=>{if(e.name!=='AbortError')console.error('proto',e);});
157|154|  }else{
158|155|    if(statsCache?.protocols){
159|156|      Object.entries(statsCache.protocols).forEach(([code,count])=>{
160|157|        if(code==='?')return;
161|158|        const opt=document.createElement('option');
162|159|        opt.value=code;
163|160|        opt.textContent=code==='http'?'HTTP':code==='https'?'HTTPS':code==='socks5'?'SOCKS5':'SOCKS4'+' ('+count+')';
164|161|        sel.appendChild(opt);
165|162|      });
166|163|    }
167|164|    sel.value=prev;
168|165|  }
169|166|}
170|167|
171|168|function renderTable(proxies){
172|169|  const tbody=document.querySelector('tbody');
173|170|  if(!proxies.length){tbody.innerHTML='<tr><td colspan="10">无数据</td></tr>';return;}
174|171|  const gradeLabel={s:'S',a:'A',b:'B',c:'C',d:'D'};
175|172|  let html='';
176|173|  for(const p of proxies){
177|174|    let regionDisp=v(COUNTRY_NAME[p.region]||p.region);
178|175|    if(p.region==='CN'&&p.location&&p.location!=='?'){
179|176|      regionDisp=COUNTRY_NAME.CN+'·'+p.location;
180|177|    }else if(p.location&&p.location!=='?'){
181|178|      regionDisp=v(COUNTRY_NAME[p.region]||p.location||p.region);
182|179|    }
183|180|    html+='<tr>'+
184|181|      `<td>${esc(p.ip)} <button class="copy-btn" data-copy="${escAttr(p.ip+':'+p.port)}" title="复制">📋</button></td>`+
185|182|      `<td>${esc(String(p.port))}</td>`+
186|183|      `<td>${esc(v(p.protocol))}</td>`+
187|184|      `<td><span class="badge ${escAttr(p.grade||'c')}">${v(gradeLabel[p.grade])}</span></td>`+
188|185|      `<td>${esc((p.delay||0).toFixed(0))}ms</td>`+
189|186|      `<td>${esc(regionDisp)}</td>`+
190|187|      `<td>${esc(v(p.region))}</td>`+
191|188|      `<td>${esc(v(p.source))}</td>`+
192|189|      `<td>${esc(v(p.anon))}</td>`+
193|190|      `<td>${esc(String(v(p.last_check)).slice(0,10))}</td>`+
194|191|      '</tr>';
195|192|  }
196|193|  tbody.innerHTML=html;
197|194|}
198|195|
199|196|function renderPagination(){
200|197|  const nav=document.getElementById('pagination');
201|198|  if(!nav)return;
202|199|  const{page,pages}=STATE;
203|200|  if(pages<=1){nav.innerHTML='';return;}
204|201|  let html='';
205|202|  let start=Math.max(1,page-Math.floor(MAX_PAGE/2));
206|203|  let end=Math.min(pages,start+MAX_PAGE-1);
207|204|  if(end-start+1<MAX_PAGE)start=Math.max(1,end-MAX_PAGE+1);
208|205|  if(page>1)html+=`<a href="#" data-page="1">‹</a><a href="#" data-page="${page-1}">‹</a>`;
209|206|  for(let i=start;i<=end;i++){
210|207|    if(i===page)html+=`<span class="cur">${i}</span>`;
211|208|    else html+=`<a href="#" data-page="${i}">${i}</a>`;
212|209|  }
213|210|  if(page<pages)html+=`<a href="#" data-page="${page+1}">›</a><a href="#" data-page="${pages}">»</a>`;
214|211|  nav.innerHTML=html;
215|212|}
216|213|
217|214|function onFilterChange(){
218|215|  readFilters();
219|216|  STATE.page=1;
220|217|  loadData();
221|218|}
222|219|
223|220|function onSort(col){
224|221|  if(STATE.sort===col)STATE.asc=!STATE.asc;
225|222|  else{STATE.sort=col;STATE.asc=true;}
226|223|  loadData();
227|224|}
228|225|
229|226|function onPageClick(ev){
230|227|  const el=ev.target.closest('a[data-page],button[data-page]');
231|228|  if(!el)return;
232|229|  ev.preventDefault();
233|230|  STATE.page=parseInt(el.dataset.page);
234|231|  loadData();
235|232|}
236|233|
237|234|function onCopy(ev){
238|235|  const btn=ev.target.closest('.copy-btn');
239|236|  if(!btn)return;
240|237|  const text=btn.dataset.copy;
241|238|  navigator.clipboard.writeText(text).then(()=>{
242|239|    const toast=document.getElementById('toast')||(()=>{const t=document.createElement('div');t.id='toast';document.body.appendChild(t);return t;})();
243|240|    toast.textContent='已复制 '+text;
244|241|    toast.classList.add('show');
245|242|    setTimeout(()=>toast.classList.remove('show'),1500);
246|243|  }).catch(()=>{
247|244|    const ta=document.createElement('textarea');
248|245|    ta.value=text;ta.style.position='fixed';ta.style.opacity=0;
249|246|    document.body.appendChild(ta);ta.select();
250|247|    document.execCommand('copy');document.body.removeChild(ta);
251|248|  });
252|249|}
253|250|
254|251|function onKeydown(ev){
255|252|  const tag=ev.target.tagName;
256|253|  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT')return;
257|254|  if(ev.key==='ArrowLeft'){if(STATE.page>1){STATE.page--;loadData();}}
258|255|  if(ev.key==='ArrowRight'){if(STATE.page<STATE.pages){STATE.page++;loadData();}}
259|256|}
260|257|
261|258|function init(){
262|259|  if(STATE._ready)return;STATE._ready=1;
263|260|  const countrySel=document.querySelector('[name=country]');
264|261|  const protocolSel=document.querySelector('[name=protocol]');
265|262|  countrySel?.addEventListener('change',()=>{
266|263|    readFilters();
267|264|    STATE.page=1;
268|265|    buildProtocolDropdown(STATE.filters.country);
269|266|    loadData();
270|267|  });
271|268|  protocolSel?.addEventListener('change',onFilterChange);
272|269|  document.querySelectorAll('[name=grade]').forEach(el=>el.addEventListener('change',onFilterChange));
273|270|  let delayTimer;
274|271|  document.querySelector('[name=delay]')?.addEventListener('input',ev=>{
275|272|    clearTimeout(delayTimer);
276|273|    delayTimer=setTimeout(onFilterChange,300);
277|274|  });
278|275|  let searchTimer;
279|276|  document.querySelector('[name=search]')?.addEventListener('input',ev=>{
280|277|    clearTimeout(searchTimer);
281|278|    searchTimer=setTimeout(onFilterChange,300);
282|279|  });
283|280|  document.querySelectorAll('thead th[data-sort]').forEach(th=>th.addEventListener('click',()=>onSort(th.dataset.sort)));
284|281|  document.getElementById('pagination')?.addEventListener('click',onPageClick);
285|282|  document.querySelector('tbody')?.addEventListener('click',onCopy);
286|283|  document.addEventListener('keydown',onKeydown);
287|284|  loadStats();
288|285|}
289|286|if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();