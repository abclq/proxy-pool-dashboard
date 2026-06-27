/* ── 全局状态 ── */
const PER_PAGE = 50;
let allProxies = [];
let currentPage = 1;
let currentTab = 'all';
let filters = { region: '', protocol: '', speed: '', search: '' };
let regionSet = new Set();
let checkingTimer = null;

/* ── 统计 DOM ── */
const $ = id => document.getElementById(id);
const sTotal = $('s-total'), sVerified = $('s-verified'), sS = $('s-S'), sA = $('s-A'), sB = $('s-B'), sC = $('s-C');
const sText = $('status-text'), sSub = $('status-sub');
const progress = $('progress-section'), progressFill = $('progress-fill'), progressText = $('progress-text');
const proxyList = $('proxy-list'), filterCount = $('filtered-count'), avgLat = $('avg-latency');
const loading = $('loading'), noResults = $('no-results');
const curPage = $('current-page'), totalPages = $('total-pages');
const prevBtn = $('prev-page'), nextBtn = $('next-page');
const toast = $('toast');

/* ── 格式化 ── */
function fmtLat(ms) {
  if (ms == null || ms === undefined) return '<span class="lat-unknown">--</span>';
  if (ms < 200) return '<span class="lat-s">' + ms + 'ms</span>';
  if (ms < 500) return '<span class="lat-ok">' + ms + 'ms</span>';
  if (ms < 1000) return '<span class="lat-warn">' + ms + 'ms</span>';
  return '<span class="lat-bad">' + ms + 'ms</span>';
}
function badgeGrade(g) {
  if (g === 'S') return '<span class="badge badge-s">S 级</span>';
  if (g === 'A') return '<span class="badge badge-a">A 级</span>';
  if (g === 'B') return '<span class="badge badge-b">B 级</span>';
  return '<span class="badge badge-c">C 级</span>';
}

/* ── 数据加载 ── */
async function loadProxies() {
  try {
    const res = await fetch('/api/proxies');
    allProxies = await res.json();
    updateRegionFilter();
    render();
  } catch (e) { console.error(e); }
}

async function loadStats() {
  try {
    const res = await fetch('/api/stats');
    const s = await res.json();
    sTotal.textContent = s.total;
    sVerified.textContent = s.verified || 0;
    sS.textContent = s.S || 0;
    sA.textContent = s.A || 0;
    sB.textContent = s.B || 0;
    sC.textContent = s.C || 0;
    avgLat.textContent = s.avg_latency || '--';
    if (s.checking) {
      sText.textContent = '检测中';
      sSub.textContent = s.check_progress.current + '/' + s.check_progress.total;
      progress.classList.remove('hidden');
      const pct = s.check_progress.total > 0 ? (s.check_progress.current/s.check_progress.total*100) : 0;
      progressFill.style.width = pct + '%';
      progressText.textContent = s.check_progress.current + '/' + s.check_progress.total;
    } else {
      sText.textContent = '就绪';
      sSub.textContent = s.last_check ? new Date(s.last_check*1000).toLocaleTimeString('zh-CN') : '-';
      progress.classList.add('hidden');
    }
  } catch (e) { console.error(e); }
}

/* ── 地区筛选器 ── */
function updateRegionFilter() {
  regionSet.clear();
  for (const p of allProxies) {
    if (p.is_china && p.region_label) regionSet.add('🇨🇳 ' + p.region_label);
    else if (!p.is_china && p.country) regionSet.add(p.country);
  }
  const sel = $('region-filter');
  const current = sel.value;
  sel.innerHTML = '<option value="">所有地区</option>';
  [...regionSet].sort().forEach(r => {
    sel.innerHTML += '<option value="' + r + '">' + r + '</option>';
  });
  sel.value = current;
}

/* ── 筛选 ── */
function applyFilters() {
  filters.region = $('region-filter').value.replace(/^🇨🇳 /, '');
  filters.protocol = $('protocol-filter').value;
  filters.speed = $('speed-filter').value;
  filters.search = $('search-input').value.toLowerCase().trim();
  currentPage = 1;
  render();
}
document.querySelectorAll('.filter-item select, .filter-item input').forEach(el => {
  el.addEventListener('change', applyFilters);
  if (el.tagName === 'INPUT') el.addEventListener('input', applyFilters);
});

function getFiltered() {
  let list = allProxies;
  if (currentTab === 'china') list = list.filter(p => p.is_china);
  else if (currentTab === 'foreign') list = list.filter(p => !p.is_china);
  else if (currentTab === 's') list = list.filter(p => p.speed === 'S');
  else if (currentTab === 'regions') return [];
  else if (currentTab === 'nodes') return [];
  if (filters.protocol) list = list.filter(p => p.protocol === filters.protocol);
  if (filters.speed) list = list.filter(p => p.speed === filters.speed);
  if (filters.region) {
    list = list.filter(p => (p.is_china && p.region_label === filters.region) || (!p.is_china && p.country === filters.region));
  }
  if (filters.search) list = list.filter(p => p.ip.includes(filters.search));
  return list;
}

/* ── 渲染 ── */
function render() {
  if (currentTab === 'regions') { renderRegions(); return; }
  if (currentTab === 'nodes') { renderNodes(); return; }
  const filtered = getFiltered();
  const total = filtered.length;
  const pages = Math.max(1, Math.ceil(total / PER_PAGE));
  if (currentPage > pages) currentPage = pages;
  const start = (currentPage - 1) * PER_PAGE;
  const pageData = filtered.slice(start, start + PER_PAGE);
  filterCount.textContent = total;
  curPage.textContent = currentPage;
  totalPages.textContent = pages;
  prevBtn.disabled = currentPage <= 1;
  nextBtn.disabled = currentPage >= pages;
  const validLats = pageData.filter(p => p.latency != null).map(p => p.latency);
  avgLat.textContent = validLats.length ? (validLats.reduce((a,b)=>a+b,0)/validLats.length).toFixed(1) : '--';
  proxyList.innerHTML = pageData.map(p =>
    '<tr><td><code>' + p.ip + '</code></td><td>' + p.port + '</td><td>' +
    (p.protocol === 'https' ? '🔒 HTTPS' : 'HTTP') + '</td><td>' +
    (p.is_china ? '<span class="badge-cn">🇨🇳 ' + p.region_label + '</span>' : '<span class="badge-intl">' + p.country + '</span>') +
    '</td><td>' + badgeGrade(p.speed) + '</td><td>' + fmtLat(p.latency) + '</td><td><span style="color:var(--text2)">' +
    (p.source||'未知') + '</span></td><td><button class="btn-copy" onclick="copier(' + JSON.stringify(p.proxy) +
    ')" title="复制">📋</button></td></tr>'
  ).join('');
  if (total === 0) {
    proxyList.innerHTML = '';
    noResults.classList.remove('hidden');
    loading.classList.add('hidden');
  } else {
    noResults.classList.add('hidden');
    loading.classList.add('hidden');
  }
}

/* ── 地区分布页 ── */
function renderRegions() {
  noResults.classList.add('hidden'); loading.classList.add('hidden');
  const chinaStats = {}, foreignStats = {};
  for (const p of allProxies) {
    if (p.is_china) {
      const city = p.region_label || '未知';
      if (!chinaStats[city]) chinaStats[city] = { count: 0, S: 0, A: 0, B: 0, C: 0, lats: [] };
      chinaStats[city].count++;
      chinaStats[city][p.speed || 'C']++;
      if (p.latency != null) chinaStats[city].lats.push(p.latency);
    } else {
      const c = p.country || '未知';
      if (!foreignStats[c]) foreignStats[c] = { count: 0, S: 0, A: 0, B: 0, C: 0, lats: [] };
      foreignStats[c].count++;
      foreignStats[c][p.speed || 'C']++;
      if (p.latency != null) foreignStats[c].lats.push(p.latency);
    }
  }
  filterCount.textContent = allProxies.length;
  avgLat.textContent = '--';
  const cnSorted = Object.entries(chinaStats).sort((a,b) => b[1].count - a[1].count);
  const intlSorted = Object.entries(foreignStats).sort((a,b) => b[1].count - a[1].count);
  let html = '';
  if (cnSorted.length) {
    html += '<tr class="section-hdr"><td colspan="8" style="color:var(--china);font-weight:700;padding:12px 0 6px;">🇨🇳 中国代理 · 按城市</td></tr>';
    for (const [city, s] of cnSorted) {
      const avg = s.lats.length ? (s.lats.reduce((a,b)=>a+b,0)/s.lats.length).toFixed(0) : '--';
      html += '<tr><td colspan="3"><strong>' + city + '</strong></td><td>代理 ' + s.count + '</td>' +
        '<td>' + badgeGradeN('S',s.S) + '</td><td>' + badgeGradeN('A',s.A) + '</td>' +
        '<td>' + badgeGradeN('B',s.B) + '</td><td>' + badgeGradeN('C',s.C) + '</td><td style="color:var(--text2)">均' + avg + 'ms</td></tr>';
    }
  }
  if (intlSorted.length) {
    html += '<tr class="section-hdr"><td colspan="8" style="color:var(--accent);font-weight:700;padding:12px 0 6px;">🌍 国外代理 · 按国家</td></tr>';
    for (const [c, s] of intlSorted) {
      const avg = s.lats.length ? (s.lats.reduce((a,b)=>a+b,0)/s.lats.length).toFixed(0) : '--';
      html += '<tr><td colspan="3"><strong>' + c + '</strong></td><td>代理 ' + s.count + '</td>' +
        '<td>' + badgeGradeN('S',s.S) + '</td><td>' + badgeGradeN('A',s.A) + '</td>' +
        '<td>' + badgeGradeN('B',s.B) + '</td><td>' + badgeGradeN('C',s.C) + '</td><td style="color:var(--text2)">均' + avg + 'ms</td></tr>';
    }
  }
  proxyList.innerHTML = html;
}

function badgeGradeN(grade, count) {
  if (count === 0) return '';
  const map = { S: ['badge-s','S'], A: ['badge-a','A'], B: ['badge-b','B'], C: ['badge-c','C'] };
  const m = map[grade] || ['badge-c','C'];
  return '<span class="badge ' + m[0] + '">' + m[1] + ' ' + count + '</span>';
}

/* ── 分页 ── */
prevBtn.addEventListener('click', () => { if (currentPage > 1) { currentPage--; render(); } });
nextBtn.addEventListener('click', () => {
  const filtered = getFiltered();
  const pages = Math.ceil(filtered.length / PER_PAGE);
  if (currentPage < pages) { currentPage++; render(); }
});

/* ── 导航 ── */
document.querySelectorAll('.nav-item[data-tab]').forEach(el => {
  el.addEventListener('click', e => {
    e.preventDefault();
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    el.classList.add('active');
    currentTab = el.dataset.tab;
    currentPage = 1;
    if (currentTab === 'regions' || currentTab === 'nodes') {
      $('region-filter').value = '';
      $('protocol-filter').value = '';
      $('speed-filter').value = '';
      $('search-input').value = '';
      filters = { region: '', protocol: '', speed: '', search: '' };
    }
    render();
  });
});

/* ── 复制 ── */
function copier(text) {
  navigator.clipboard.writeText(text).then(() => {
    toast.classList.remove('hidden');
    setTimeout(() => toast.classList.add('hidden'), 1500);
  });
}

/* ── 导出 ── */
function doExport(qs) {
  const a = document.createElement('a');
  a.href = '/api/export?' + qs;
  a.download = 'proxies.txt';
  a.click();
}
$('exp-all').addEventListener('click', () => doExport(''));
$('exp-http').addEventListener('click', () => doExport('protocol=http'));
$('exp-https').addEventListener('click', () => doExport('protocol=https'));
$('exp-s').addEventListener('click', () => doExport('speed=S'));
$('exp-china').addEventListener('click', () => doExport('china=1'));

$('refresh-btn').addEventListener('click', () => {
  fetch('/api/check', { method: 'POST' }).catch(() => {});
  setTimeout(() => { loadProxies(); loadStats(); }, 1000);
});

/* ── 代理服务控制 ── */
function toggleService(service) {
  const btn = $('btn-' + service);
  const isRunning = btn.classList.contains('running');
  const action = isRunning ? 'stop' : 'start';
  btn.disabled = true;
  fetch('/api/services', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: action, service: service }),
  }).then(r => r.json()).then(s => updateServiceUI(s)).finally(() => { btn.disabled = false; });
}
document.querySelectorAll('.btn-service').forEach(btn => {
  btn.addEventListener('click', () => { toggleService(btn.dataset.service); });
});

async function updateServiceUI(s) {
  const btnHttp = $('btn-http'), statusHttp = $('status-http');
  if (s.http.running) { btnHttp.classList.add('running'); btnHttp.textContent = '停止'; statusHttp.textContent = '✅ :' + s.http.port; }
  else { btnHttp.classList.remove('running'); btnHttp.textContent = '启动'; statusHttp.textContent = ':' + s.http.port; }
  const btnS5 = $('btn-socks5'), statusS5 = $('status-socks5');
  if (s.socks5.running) { btnS5.classList.add('running'); btnS5.textContent = '停止'; statusS5.textContent = '✅ :' + s.socks5.port; }
  else { btnS5.classList.remove('running'); btnS5.textContent = '启动'; statusS5.textContent = ':' + s.socks5.port; }
}

/* ── 节点选择 ── */
const nodePicker = $('node-picker');
const selectedNode = $('selected-node');
const btnClearNode = $('btn-clear-node');

async function loadNodeSelector() {
  try {
    const r = await fetch('/api/select');
    const s = await r.json();
    if (s.selected && s.info) {
      selectedNode.className = 'selected-node';
      let g = (s.info.speed || 'C').toLowerCase();
      selectedNode.innerHTML = '🎯 ' + s.info.ip + ':' + s.info.port + ' <span class="badge badge-' + g + '">' + (s.info.speed||'C') + '级 ' + (s.info.latency||'?') + 'ms</span>';
      btnClearNode.disabled = false;
    } else {
      selectedNode.className = 'selected-node none';
      selectedNode.textContent = '🔄 自动轮换';
      btnClearNode.disabled = true;
    }
    nodePicker.innerHTML = '<option value="">-- 选择节点 --</option>';
    for (const p of (s.available || [])) {
      var label = (p.speed||'C') + ' ' + (p.latency||'?') + 'ms | ' + (p.region||p.country||'未知') + ' | ' + p.proxy;
      var sel = s.selected === p.proxy ? ' selected' : '';
      nodePicker.innerHTML += '<option value="' + p.proxy + '"' + sel + '>' + label + '</option>';
    }
  } catch(e) { console.error(e); }
}
nodePicker.addEventListener('change', async () => {
  var proxy = nodePicker.value;
  if (!proxy) return;
  await fetch('/api/select', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'set', proxy:proxy}) });
  loadNodeSelector();
});
btnClearNode.addEventListener('click', async () => {
  await fetch('/api/select', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'clear'}) });
  nodePicker.value = '';
  loadNodeSelector();
});

/* ── 路由策略 ── */
const strategyPicker = $('strategy-picker');
if (strategyPicker) {
  strategyPicker.addEventListener('change', async () => {
    await fetch('/api/strategy', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({strategy: strategyPicker.value}) });
  });
}
async function loadStrategy() {
  try {
    var r = await fetch('/api/strategy');
    var s = await r.json();
    if (strategyPicker && s.strategy) strategyPicker.value = s.strategy;
  } catch(e) {}
}

/* ── 远程节点 ── */
async function renderNodes() {
  noResults.classList.add('hidden'); loading.classList.add('hidden');
  try {
    var r = await fetch('/api/nodes');
    var s = await r.json();
    var nodes = s.nodes || {};
    var names = Object.keys(nodes);
    filterCount.textContent = names.length;
    avgLat.textContent = '--';
    if (names.length === 0) {
      proxyList.innerHTML = '<tr><td colspan="4" style="color:var(--text2);padding:40px;text-align:center">暂无远程节点<br><small>使用 API 添加: POST /api/nodes {action:"add",name:"xxx",url:"..."}</small></td></tr>';
      return;
    }
    var html = '';
    for (var name of names) {
      var n = nodes[name];
      var icon = n.status === 'ok' ? '✅' : n.status === 'down' ? '❌' : '⏳';
      html += '<tr><td><strong>' + name + '</strong></td><td><code>' + n.url + '</code></td><td>' + icon + ' ' + n.status + '</td><td>代理 ' + (n.proxy_count||0) + '</td></tr>';
    }
    proxyList.innerHTML = html;
  } catch(e) { console.error(e); }
}

/* ── 初始化 ── */
async function init() {
  await loadProxies();
  await loadStats();
  await loadNodeSelector();
  await loadStrategy();
  try {
    const r = await fetch('/api/services');
    const s = await r.json();
    updateServiceUI(s);
  } catch (e) {}
  setInterval(() => { loadProxies(); loadStats(); loadNodeSelector(); }, 30000);
}
init();