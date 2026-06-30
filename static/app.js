// ── State ──
let currentCountry = null;
let currentPage = 1;
let countries = [];
let allCountries = [];
let debounceTimer = null;

// ── Init ──
(async function init() {
  document.getElementById('title').onclick = goBack;
  await loadStats();
  await loadCountries();
})();

// ── Stats bar ──
async function loadStats() {
  try {
    const r = await fetch('/api/stats');
    const d = await r.json();
    const bar = document.getElementById('stats-bar');
    bar.textContent = `共 ${d.total.toLocaleString()} 节点 · ${Object.keys(d.regions||{}).length} 个国家 · 仅展示 <500ms`;
  } catch(e) {}
}

// ── Countries ──
async function loadCountries() {
  try {
    const r = await fetch('/api/countries');
    const d = await r.json();
    allCountries = d.countries || [];
    countries = [...allCountries];
    renderCountries();
  } catch(e) {
    document.getElementById('country-grid').innerHTML = '<p style="text-align:center;color:#888">加载失败</p>';
  }
}

function filterCountries() {
  const q = (document.getElementById('country-search').value || '').toLowerCase();
  if (!q) { countries = [...allCountries]; }
  else { countries = allCountries.filter(c => c.name.toLowerCase().includes(q) || c.code.toLowerCase().includes(q)); }
  renderCountries();
}

function flagEmoji(code) {
  if (!code || code === '?' || code.length !== 2) return '🏳️';
  const a = code.toUpperCase();
  return String.fromCodePoint(0x1F1E6 + a.charCodeAt(0) - 65, 0x1F1E6 + a.charCodeAt(1) - 65);
}

function renderCountries() {
  const grid = document.getElementById('country-grid');
  if (!countries.length) {
    grid.innerHTML = '<p style="text-align:center;color:#888">无匹配国家</p>';
    return;
  }
  grid.innerHTML = countries.map(c => {
    const flag = flagEmoji(c.code);
    return `<div class="country-card" onclick="drillInto('${c.code}','${c.name.replace(/'/g,"\\'")}')">
      <span class="country-flag">${flag}</span>
      <span class="country-name">${c.name}</span>
      <span class="country-count">${c.count.toLocaleString()}</span>
    </div>`;
  }).join('');
}

// ── Drill ──
function drillInto(code, name) {
  currentCountry = code;
  currentPage = 1;
  document.getElementById('view-country-list').style.display = 'none';
  document.getElementById('view-country-detail').style.display = 'block';
  document.getElementById('detail-title').textContent = `${flagEmoji(code)} ${name || code}`;
  // Populate protocol filter
  const protoSel = document.getElementById('f-proto');
  protoSel.innerHTML = '<option value="">全部协议</option>' +
    ['http','https','socks4','socks5'].map(p => `<option value="${p}">${p.toUpperCase()}</option>`).join('');
  loadDetail(1);
}

function goBack() {
  currentCountry = null;
  document.getElementById('view-country-list').style.display = 'block';
  document.getElementById('view-country-detail').style.display = 'none';
}

function debounceLoad() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => loadDetail(1), 400);
}

async function loadDetail(page) {
  if (!currentCountry) return;
  currentPage = page;
  const proto = document.getElementById('f-proto').value;
  const loc = document.getElementById('f-location').value.trim();
  const delay = document.getElementById('f-delay').value.trim();
  const sort = document.getElementById('f-sort').value;
  const search = document.getElementById('f-search').value.trim();

  const params = new URLSearchParams();
  params.set('page', page);
  params.set('limit', 50);
  if (proto) params.set('protocol', proto);
  if (loc) params.set('location', loc);
  if (delay) params.set('delay', delay);
  if (search) params.set('search', search);
  if (sort) { const [by, dir] = sort.split('-'); params.set('sort', by); params.set('asc', dir === 'asc' ? '1' : '0'); }

  try {
    const r = await fetch(`/api/country/${currentCountry}?${params}`);
    const d = await r.json();
    renderDetailTable(d);
  } catch(e) {
    document.getElementById('detail-tbody').innerHTML = '<tr><td colspan="5" style="color:#888">加载失败</td></tr>';
  }
}

function renderDetailTable(d) {
  document.getElementById('detail-info').textContent =
    `共 ${d.total_matched.toLocaleString()} 条 · 第 ${d.page}/${d.pages} 页`;

  const proxies = d.proxies || [];
  const tbody = document.getElementById('detail-tbody');
  if (!proxies.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#888">无代理</td></tr>';
  } else {
    tbody.innerHTML = proxies.map(p => {
      const addr = `${p.ip}:${p.port}`;
      const lat = p.delay > 0 ? p.delay + 'ms' : '-';
      const cls = p.delay < 200 ? 'fast' : p.delay < 350 ? 'mid' : '';
      return `<tr>
        <td class="proxy-addr" onclick="copyAddr('${addr}')" title="点击复制">${addr}</td>
        <td><span class="proto-badge proto-${p.protocol}">${p.protocol.toUpperCase()}</span></td>
        <td class="lat-${cls}">${lat}</td>
        <td>${p.location || '-'}</td>
        <td class="time-col">${p.last_check || '-'}</td>
      </tr>`;
    }).join('');
  }

  // Pager
  const pager = document.getElementById('detail-pager');
  if (d.pages <= 1) { pager.innerHTML = ''; return; }
  let html = `<button ${d.page<=1?'disabled':''} onclick="loadDetail(${d.page-1})">‹</button>`;
  for (let i = Math.max(1, d.page - 3); i <= Math.min(d.pages, d.page + 3); i++) {
    html += `<button class="${i===d.page?'active':''}" onclick="loadDetail(${i})">${i}</button>`;
  }
  html += `<button ${d.page>=d.pages?'disabled':''} onclick="loadDetail(${d.page+1})">›</button>`;
  pager.innerHTML = html;
}

function copyAddr(addr) {
  navigator.clipboard.writeText(addr).then(() => {
    // Brief visual feedback
    const el = document.querySelector('.proxy-addr.copied');
    if (el) el.classList.remove('copied');
    const clicked = event.target;
    clicked.classList.add('copied');
    setTimeout(() => clicked.classList.remove('copied'), 800);
  }).catch(() => {});
}
