'use strict';

// ─── State ───────────────────────────────────────────────────────────────────
const _S = {
  page: 'outreach',
  accounts: [],
  imports: [],
  selectedAccountId: null,
  selectedImportId: null,
  outreachRunning: false,
  monitorRunning: false,
  outreachTimer: null,
  monitorTimer: null,
  monitorCheckInterval: 120,
  logSince: 0,
  logTimer: null,
  logFilter: 'all',
  autoScroll: true,
  activity: [],
  replies: [],
  outreachProgress: { sent: 0, target: 100 },
  outreachConfig: {},
};

// ─── Helpers ─────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const qsa = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));
const pad2 = (n) => String(n).padStart(2, '0');
const nowStr = () => { const d = new Date(); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };
const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

function lvlKey(level) {
  const l = (level || '').toLowerCase();
  if (l === 'warning' || l === 'warn') return 'warn';
  if (l === 'error' || l === 'err' || l === 'critical') return 'err';
  return 'info';
}

async function api(path, opts = {}) {
  const fetchOpts = { method: opts.method || 'GET' };
  if (opts.body !== undefined) {
    fetchOpts.headers = { 'Content-Type': 'application/json' };
    fetchOpts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, fetchOpts);
  return res.json();
}

// ─── Navigation ──────────────────────────────────────────────────────────────
const PAGES = ['settings', 'database', 'outreach', 'monitor', 'log'];

function navigate(page) {
  if (!PAGES.includes(page)) return;
  _S.page = page;

  qsa('.tab').forEach(t => t.classList.toggle('is-active', t.dataset.tab === page));
  qsa('.page').forEach(p => { p.style.display = 'none'; });

  const pageEl = $(`page-${page}`);
  if (pageEl) pageEl.style.display = 'block';

  const sbPage = $('statusbar-page');
  if (sbPage) sbPage.textContent = page;
  const menuPage = $('menuPage');
  if (menuPage) menuPage.textContent = page;

  const tabs = $('tabs');
  if (tabs) tabs.classList.remove('is-open');

  // Stop log polling when leaving log page
  if (page !== 'log' && _S.logTimer) {
    clearInterval(_S.logTimer);
    _S.logTimer = null;
  }

  if (page === 'settings') loadSettings();
  else if (page === 'database') loadDatabase();
  else if (page === 'outreach') loadOutreach();
  else if (page === 'monitor') loadMonitor();
  else if (page === 'log') initLog();
}

// ─── Clock ───────────────────────────────────────────────────────────────────
function startClock() {
  const tick = () => { const el = $('clock'); if (el) el.textContent = nowStr(); };
  tick();
  setInterval(tick, 1000);
  // Keep cursor time updated
  setInterval(() => { const el = $('log-cursor-time'); if (el) el.textContent = nowStr(); }, 1000);
}

// ─── Status polling ───────────────────────────────────────────────────────────
function setDot(id, on) {
  const el = $(id);
  if (!el) return;
  el.className = `dot dot--${on ? 'on' : 'off'}`;
}

async function pollStatus() {
  try {
    const d = await api('/api/status');
    setDot('dot-outreach', d.outreach_running);
    setDot('dot-monitor', d.monitor_running);
    const lo = $('lbl-outreach'); if (lo) lo.textContent = d.outreach_running ? 'running' : 'idle';
    const lm = $('lbl-monitor'); if (lm) lm.textContent = d.monitor_running ? 'running' : 'idle';

    // Sync state if server says something changed (e.g. daily limit hit)
    if (_S.outreachRunning && !d.outreach_running && _S.page === 'outreach') {
      _S.outreachRunning = false;
      clearTimeout(_S.outreachTimer);
      _S.outreachTimer = null;
      syncOutreachBtn();
    }

    // Update statusbar account count
    const sbAcct = $('sb-accounts');
    if (sbAcct && typeof d.sent_today === 'number') {
      sbAcct.textContent = `${d.sent_today} sent today`;
    }
  } catch {}
}

// ─── Settings ─────────────────────────────────────────────────────────────────
async function loadSettings() {
  await Promise.all([fetchAccounts(), fetchDiscord()]);
}

async function fetchAccounts() {
  try {
    const accounts = await api('/api/accounts');
    _S.accounts = accounts;
    renderAccountList();
    const sbAcct = $('sb-accounts');
    if (sbAcct) sbAcct.textContent = `${accounts.length} account${accounts.length !== 1 ? 's' : ''} linked`;
  } catch {}
}

function renderAccountList() {
  const list = $('acct-list');
  if (!list) return;
  const countEl = $('acct-count');
  if (countEl) countEl.textContent = `${_S.accounts.length} / 10`;

  list.innerHTML = _S.accounts.map(a => `
    <button class="acct${_S.selectedAccountId === a.id ? ' is-sel' : ''}" onclick="selectAccount('${esc(a.id)}')">
      <div class="acct__row">
        <span class="dot dot--on"></span>
        <span class="acct__email">${esc(a.label || a.smtp_user || a.id)}</span>
      </div>
      <div class="acct__meta">
        <span>${esc(a.smtp_user || '')}</span>
      </div>
    </button>
  `).join('') + `
    <button class="acct acct--add" onclick="addAccount()">
      <span>+ add account</span>
      <span class="acct__hint">smtp / imap</span>
    </button>
  `;
}

function selectAccount(id) {
  _S.selectedAccountId = id;
  renderAccountList();
  const acct = _S.accounts.find(a => a.id === id);
  if (!acct) return;

  const emailEl = $('acct-detail-email');
  if (emailEl) emailEl.textContent = acct.smtp_user || acct.label || '—';

  const detail = $('acct-detail');
  if (!detail) return;
  detail.innerHTML = `
    <div class="field"><label>label</label><input id="ed-label" value="${esc(acct.label || '')}"></div>
    <div class="field"><label>smtp host</label><input id="ed-smtp-host" value="${esc(acct.smtp_host || '')}"></div>
    <div class="field field--small"><label>smtp port</label><input id="ed-smtp-port" value="${esc(acct.smtp_port || '')}"></div>
    <div class="field"><label>smtp user (email)</label><input id="ed-smtp-user" value="${esc(acct.smtp_user || '')}"></div>
    <div class="field"><label>smtp password</label><input type="password" id="ed-smtp-pass" value="${esc(acct.smtp_password || '')}"></div>
    <div class="field"><label>display name</label><input id="ed-display-name" value="${esc(acct.display_name || '')}"></div>
    <div class="field"><label>imap host</label><input id="ed-imap-host" value="${esc(acct.imap_host || '')}"></div>
    <div class="field field--small"><label>imap port</label><input id="ed-imap-port" value="${esc(acct.imap_port || '')}"></div>
    <div class="field"><label>outreach subject</label><input id="ed-subj" value="${esc(acct.outreach_subject || '')}"></div>
    <div class="field">
      <label>outreach body <span style="color:var(--mute)">(use {name} for personalization)</span></label>
      <textarea id="ed-body" rows="6" style="width:100%;background:var(--bg-3);border:1px solid var(--line-2);color:var(--ink);font-family:var(--mono);font-size:12px;padding:8px 10px;resize:vertical;border-radius:2px;outline:none;">${esc(acct.outreach_body || '')}</textarea>
    </div>
    <div class="detail__actions">
      <button class="btn btn--primary" onclick="saveAccount()">save</button>
      <button class="btn btn--danger" onclick="deleteAccount('${esc(acct.id)}')">remove</button>
    </div>
  `;
}

async function addAccount() {
  try {
    const acct = await api('/api/accounts', { method: 'POST', body: {} });
    await fetchAccounts();
    selectAccount(acct.id);
  } catch(e) { alert('Error adding account: ' + e.message); }
}

async function saveAccount() {
  if (!_S.selectedAccountId) return;
  const g = (id) => ($(id) || {}).value || '';
  const data = {
    label: g('ed-label'),
    smtp_host: g('ed-smtp-host'),
    smtp_port: g('ed-smtp-port'),
    smtp_user: g('ed-smtp-user'),
    smtp_password: g('ed-smtp-pass'),
    display_name: g('ed-display-name'),
    imap_host: g('ed-imap-host'),
    imap_port: g('ed-imap-port'),
    outreach_subject: g('ed-subj'),
    outreach_body: g('ed-body'),
  };
  try {
    await api(`/api/accounts/${_S.selectedAccountId}`, { method: 'PUT', body: data });
    await fetchAccounts();
    selectAccount(_S.selectedAccountId);
  } catch(e) { alert('Error saving account: ' + e.message); }
}

async function deleteAccount(id) {
  if (!confirm('Remove this account?')) return;
  try {
    await api(`/api/accounts/${id}`, { method: 'DELETE' });
    _S.selectedAccountId = null;
    const emailEl = $('acct-detail-email'); if (emailEl) emailEl.textContent = '—';
    const detail = $('acct-detail'); if (detail) detail.innerHTML = '<p style="color:var(--mute);font-size:12px;padding:16px 0">select an account to edit</p>';
    await fetchAccounts();
  } catch(e) { alert('Error removing account: ' + e.message); }
}

async function fetchDiscord() {
  try {
    const data = await api('/api/discord');
    const el = $('discord-url'); if (el) el.value = data.webhook || '';
    const sbWh = $('sb-webhook');
    if (sbWh) sbWh.textContent = data.webhook ? 'webhook ok' : 'webhook —';
  } catch {}
}

async function saveDiscord() {
  const url = ($('discord-url') || {}).value || '';
  try {
    await api('/api/discord', { method: 'POST', body: { webhook: url } });
    const sbWh = $('sb-webhook'); if (sbWh) sbWh.textContent = url ? 'webhook ok' : 'webhook —';
  } catch(e) { alert('Error saving webhook: ' + e.message); }
}

async function testWebhook() {
  await saveDiscord();
  const btn = $('btn-test-webhook');
  if (btn) { btn.textContent = 'sent!'; setTimeout(() => { btn.textContent = 'test ping'; }, 2000); }
}

// ─── Database ─────────────────────────────────────────────────────────────────
async function loadDatabase() {
  try {
    const imports = await api('/api/imports');
    _S.imports = imports;
    renderImportList();
    if (!_S.selectedImportId && imports.length > 0) {
      showImportDetail(imports[0].id);
    } else if (_S.selectedImportId) {
      showImportDetail(_S.selectedImportId);
    }
  } catch {}
}

function renderImportList() {
  const listEl = $('import-list');
  if (!listEl) return;
  const q = (($('import-search') || {}).value || '').toLowerCase();
  const filtered = _S.imports.filter(i => (i.label || '').toLowerCase().includes(q) || String(i.id).includes(q));
  const countEl = $('import-count');
  if (countEl) countEl.textContent = `${filtered.length} list${filtered.length !== 1 ? 's' : ''}`;

  if (!filtered.length) {
    listEl.innerHTML = '<div style="color:var(--mute);font-size:11px;padding:12px 0">no imports found</div>';
    return;
  }

  listEl.innerHTML = filtered.map(imp => {
    const st = imp.stats || {};
    const total = st.total || 0;
    const sent = st.sent || 0;
    const pct = total > 0 ? Math.round(sent / total * 100) : 0;
    const date = (imp.imported_at || '').slice(0, 10);
    return `
      <button class="imp${_S.selectedImportId === imp.id ? ' is-sel' : ''}" onclick="showImportDetail(${imp.id})">
        <div class="imp__top">
          <span class="imp__id">#${imp.id}</span>
          <span class="imp__name">${esc(imp.label || `Import #${imp.id}`)}</span>
          <span class="imp__leads">${total.toLocaleString()}</span>
        </div>
        <div class="imp__bot">
          <span class="imp__date">${date}</span>
          <div class="imp__bar"><div class="imp__fill" style="width:${pct}%"></div></div>
          <span class="imp__pct">${pct}%</span>
        </div>
      </button>
    `;
  }).join('');
}

async function showImportDetail(importId) {
  _S.selectedImportId = importId;
  renderImportList();

  const imp = _S.imports.find(i => i.id === importId);
  if (!imp) return;

  const st = imp.stats || {};
  const total = st.total || 0;
  const sent = st.sent || 0;
  const replied = st.replied || 0;
  const left = st.left != null ? st.left : Math.max(0, total - sent);
  const date = (imp.imported_at || '').slice(0, 10);

  const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  setTxt('db-detail-title', imp.label || `Import #${imp.id}`);
  setTxt('db-detail-meta', `id #${imp.id} · ${date}`);
  setTxt('db-stat-total', total.toLocaleString());
  setTxt('db-stat-sent', sent.toLocaleString());
  setTxt('db-stat-replied', replied.toLocaleString());
  setTxt('db-stat-left', left.toLocaleString());

  const locEl = $('loc-breakdown');
  if (locEl) {
    locEl.innerHTML = '<div style="color:var(--mute);font-size:11px">loading…</div>';
    try {
      const data = await api(`/api/imports/${importId}/locations`);
      const locs = (data.locations || []).slice(0, 12);
      if (!locs.length) {
        locEl.innerHTML = '<div style="color:var(--mute);font-size:11px">no location data</div>';
      } else {
        const max = Math.max(...locs.map(l => l.count), 1);
        locEl.innerHTML = locs.map(l => {
          const pct = total > 0 ? Math.round(l.count / total * 100) : 0;
          const barW = Math.round(l.count / max * 100);
          return `<div class="loc">
            <span class="loc__c">${esc(l.country)}</span>
            <div class="loc__track"><div class="loc__fill" style="width:${barW}%"></div></div>
            <span class="loc__n">${l.count}</span>
            <span class="loc__pct">${pct}%</span>
          </div>`;
        }).join('');
      }
    } catch {
      locEl.innerHTML = '<div style="color:var(--mute);font-size:11px">—</div>';
    }
  }
}

function deleteSelectedImport() {
  if (!_S.selectedImportId) { alert('Select an import first.'); return; }
  if (!confirm(`Delete import #${_S.selectedImportId}? This cannot be undone.`)) return;
  api(`/api/imports/${_S.selectedImportId}`, { method: 'DELETE' })
    .then(() => { _S.selectedImportId = null; loadDatabase(); })
    .catch(e => alert('Delete failed: ' + e.message));
}

// ─── Outreach ─────────────────────────────────────────────────────────────────
async function loadOutreach() {
  try {
    const [config, status] = await Promise.all([
      api('/api/outreach/config'),
      api('/api/outreach/status'),
    ]);
    _S.outreachConfig = config;
    renderOutreachConfig(config);
    applyOutreachStatus(status.running, status.sent_today, config.daily_limit || 100);
    if (!_S.activity.length) loadOutreachHistory();
  } catch(e) { console.error('loadOutreach', e); }
}

function renderOutreachConfig(cfg) {
  const sel = $('outreach-db-select');
  if (sel) {
    sel.innerHTML = '<option value="">— select import —</option>';
    (cfg.imports || []).forEach(imp => {
      const o = document.createElement('option');
      o.value = imp.id;
      o.textContent = `#${imp.id} — ${imp.label}`;
      sel.appendChild(o);
    });
    if (cfg.import_id) sel.value = String(cfg.import_id);
  }

  const setVal = (id, v) => { const el = $(id); if (el) el.value = v; };
  setVal('out-daily-limit', cfg.daily_limit || 100);
  setVal('out-delay-min', cfg.delay_min || 120);
  setVal('out-delay-max', cfg.delay_max || 300);

  const weightsEl = $('sender-weights');
  if (weightsEl) {
    weightsEl.innerHTML = (cfg.accounts || []).map(a => `
      <div class="weight">
        <span class="weight__email">${esc(a.label || a.smtp_user || '')}</span>
        <div class="weight__ctrl">
          <input type="range" min="0" max="100" value="${a.weight || 0}"
            data-acct-id="${esc(a.id)}"
            oninput="this.nextElementSibling.value=this.value">
          <input type="number" class="weight__num" min="0" max="100" value="${a.weight || 0}"
            oninput="this.previousElementSibling.value=this.value">
          <span class="weight__unit">%</span>
        </div>
      </div>
    `).join('') + '<div class="weights__hint">set to 0 to exclude an account.</div>';
  }
}

function applyOutreachStatus(running, sent, target) {
  _S.outreachRunning = !!running;
  sent = sent || 0;
  target = target || _S.outreachProgress.target || 100;
  _S.outreachProgress = { sent, target };

  const pct = target > 0 ? Math.round(sent / target * 100) : 0;
  const setTxt = (id, v) => { const el = $(id); if (el) el.textContent = v; };

  setTxt('out-progress-sent', sent.toLocaleString());
  setTxt('out-progress-target', target.toLocaleString());
  setTxt('out-progress-pct', `${pct}%`);

  const delayMin = parseInt(($('out-delay-min') || {}).value) || 120;
  const delayMax = parseInt(($('out-delay-max') || {}).value) || 300;
  const avgDelay = Math.round((delayMin + delayMax) / 2);
  setTxt('out-avg-delay', `avg ${avgDelay}s/send`);
  setTxt('out-eta', `eta ~${Math.max(0, Math.round((target - sent) * avgDelay / 60))}m`);

  const statusLabel = $('out-status-label');
  if (statusLabel) {
    statusLabel.textContent = running ? '● running' : '◌ idle';
    statusLabel.className = running ? 'acid' : '';
  }

  const fill = $('out-progress-fill');
  if (fill) fill.style.width = `${pct}%`;

  qsa('.progress__tick').forEach((t, i) => {
    t.classList.toggle('on', (i / 40) < (pct / 100));
  });

  syncOutreachBtn();
}

function syncOutreachBtn() {
  const btn = $('btn-start-outreach');
  if (!btn) return;
  btn.textContent = _S.outreachRunning ? '◼ stop' : '▶ start outreach';
  btn.className = `btn ${_S.outreachRunning ? 'btn--danger' : 'btn--acid'}`;
}

async function loadOutreachHistory() {
  try {
    const days = await api('/api/outreach/history');
    const all = [];
    (Array.isArray(days) ? days : []).forEach(day => {
      if (day && day.entries) all.push(...day.entries);
    });
    _S.activity = all.reverse().slice(0, 60);
    renderActivity();
  } catch {}
}

function prependActivity(row) {
  _S.activity.unshift(row);
  if (_S.activity.length > 60) _S.activity.pop();
  renderActivity();
}

function renderActivity() {
  const el = $('activity-rows');
  if (!el) return;
  if (!_S.activity.length) {
    el.innerHTML = '<div class="act" style="color:var(--mute);font-size:11px;grid-column:1/-1;padding:12px 0">no activity yet</div>';
    return;
  }
  el.innerHTML = _S.activity.map(r => {
    const ts = r.timestamp || r.ts || r.time || '';
    const time = ts.length > 8 ? ts.slice(11, 19) || ts.slice(0, 8) : ts;
    return `<div class="act">
      <span class="act__time">${esc(time)}</span>
      <span class="act__name">${esc(r.name || '')}</span>
      <span class="act__email">&lt;${esc(r.email || '')}&gt;</span>
      <span class="act__country">${esc(r.location || r.country || '')}</span>
      <span class="act__sender">${esc(r.sender_label || r.sender || '')}</span>
    </div>`;
  }).join('');
}

async function saveOutreachConfig() {
  const importId = parseInt(($('outreach-db-select') || {}).value) || null;
  const dailyLimit = parseInt(($('out-daily-limit') || {}).value) || 100;
  const delayMin = parseInt(($('out-delay-min') || {}).value) || 120;
  const delayMax = parseInt(($('out-delay-max') || {}).value) || 300;
  const accounts = qsa('[data-acct-id]').map(el => ({ id: el.dataset.acctId, weight: parseInt(el.value) || 0 }));
  try {
    await api('/api/outreach/config', {
      method: 'POST',
      body: { import_id: importId, daily_limit: dailyLimit, delay_min: delayMin, delay_max: delayMax, accounts },
    });
    const btn = $('btn-save-outreach');
    if (btn) { const orig = btn.textContent; btn.textContent = 'saved!'; setTimeout(() => { btn.textContent = orig; }, 1500); }
  } catch(e) { alert('Error saving config: ' + e.message); }
}

async function toggleOutreach() {
  if (_S.outreachRunning) {
    try { await api('/api/outreach/stop', { method: 'POST', body: {} }); } catch {}
    _S.outreachRunning = false;
    clearTimeout(_S.outreachTimer);
    _S.outreachTimer = null;
    applyOutreachStatus(false, _S.outreachProgress.sent, _S.outreachProgress.target);
  } else {
    const importId = parseInt(($('outreach-db-select') || {}).value) || null;
    const dailyLimit = parseInt(($('out-daily-limit') || {}).value) || 100;
    const delayMin = parseInt(($('out-delay-min') || {}).value) || 120;
    const delayMax = parseInt(($('out-delay-max') || {}).value) || 300;
    const accounts = qsa('[data-acct-id]').map(el => ({ id: el.dataset.acctId, weight: parseInt(el.value) || 0 }));
    try {
      const res = await api('/api/outreach/start', {
        method: 'POST',
        body: { import_id: importId, daily_limit: dailyLimit, delay_min: delayMin, delay_max: delayMax, accounts },
      });
      if (res.error) { alert(res.error); return; }
      _S.outreachRunning = true;
      _S.outreachProgress.target = dailyLimit;
      applyOutreachStatus(true, _S.outreachProgress.sent, dailyLimit);
      scheduleOutreachTick(res.delay_min || delayMin, res.delay_max || delayMax, true);
    } catch(e) { alert('Error starting outreach: ' + e.message); }
  }
}

function scheduleOutreachTick(dmin, dmax, immediate = false) {
  if (!_S.outreachRunning) return;
  const ms = immediate ? 0 : (dmin + Math.random() * Math.max(0, dmax - dmin)) * 1000;
  _S.outreachTimer = setTimeout(async () => {
    if (!_S.outreachRunning) return;
    try {
      const r = await api('/api/outreach/tick', { method: 'POST', body: {} });
      if (r.ok && r.contact) {
        prependActivity({
          timestamp: nowStr(),
          name: r.contact.name,
          email: r.contact.email,
          location: r.contact.location,
          sender_label: r.sender,
        });
      }
      applyOutreachStatus(!r.done && r.ok, r.sent_today, r.daily_limit || _S.outreachProgress.target);
      if (r.ok && !r.done) {
        scheduleOutreachTick(r.delay_min || dmin, r.delay_max || dmax);
      } else {
        _S.outreachRunning = false;
        syncOutreachBtn();
      }
    } catch {
      _S.outreachRunning = false;
      syncOutreachBtn();
    }
  }, ms);
}

// ─── Monitor ──────────────────────────────────────────────────────────────────
async function loadMonitor() {
  try {
    const config = await api('/api/monitor/config');
    const intvEl = $('mon-interval');
    if (intvEl) intvEl.value = config.check_interval || 120;
    _S.monitorCheckInterval = config.check_interval || 120;

    const el = $('mon-accts');
    if (el) {
      el.innerHTML = (config.accounts || []).map(a => `
        <label class="mon-acct">
          <input type="checkbox" data-mon-acct-id="${esc(a.id)}" ${(config.account_ids || []).includes(a.id) ? 'checked' : ''}>
          <span class="mon-acct__email">${esc(a.label || a.smtp_user || '')}</span>
        </label>
      `).join('');
    }

    syncMonitorBtn();
    renderReplies();
  } catch {}
}

async function saveMonitorConfig() {
  const interval = parseInt(($('mon-interval') || {}).value) || 120;
  const accountIds = qsa('[data-mon-acct-id]:checked').map(el => el.dataset.monAcctId);
  try {
    await api('/api/monitor/config', { method: 'POST', body: { check_interval: interval, account_ids: accountIds } });
    _S.monitorCheckInterval = interval;
    const btn = $('btn-save-monitor');
    if (btn) { const orig = btn.textContent; btn.textContent = 'saved!'; setTimeout(() => { btn.textContent = orig; }, 1500); }
  } catch(e) { alert('Error saving monitor config: ' + e.message); }
}

async function toggleMonitor() {
  if (_S.monitorRunning) {
    try { await api('/api/monitor/stop', { method: 'POST', body: {} }); } catch {}
    _S.monitorRunning = false;
    clearTimeout(_S.monitorTimer);
    _S.monitorTimer = null;
    syncMonitorBtn();
  } else {
    const interval = parseInt(($('mon-interval') || {}).value) || 120;
    const accountIds = qsa('[data-mon-acct-id]:checked').map(el => el.dataset.monAcctId);
    try {
      const res = await api('/api/monitor/start', {
        method: 'POST',
        body: { check_interval: interval, account_ids: accountIds },
      });
      if (res.error) { alert(res.error); return; }
      _S.monitorRunning = true;
      _S.monitorCheckInterval = res.check_interval || interval;
      syncMonitorBtn();
      scheduleMonitorTick(true);
    } catch(e) { alert('Error starting monitor: ' + e.message); }
  }
}

function syncMonitorBtn() {
  const btn = $('btn-start-monitor');
  if (!btn) return;
  btn.textContent = _S.monitorRunning ? '◼ stop' : '▶ start monitor';
  btn.className = `btn ${_S.monitorRunning ? 'btn--danger' : 'btn--acid'}`;
}

function scheduleMonitorTick(immediate = false) {
  if (!_S.monitorRunning) return;
  const ms = immediate ? 0 : _S.monitorCheckInterval * 1000;
  _S.monitorTimer = setTimeout(async () => {
    if (!_S.monitorRunning) return;
    try {
      const r = await api('/api/monitor/tick', { method: 'POST', body: {} });
      if (r.replies_found > 0) {
        prependReply({
          time: nowStr(),
          from: '(sent to discord)',
          subj: `${r.replies_found} new reply/bounce detected`,
          to: '',
          kind: 'reply',
        });
      }
    } catch {}
    if (_S.monitorRunning) scheduleMonitorTick();
  }, ms);
}

function prependReply(reply) {
  _S.replies.unshift(reply);
  if (_S.replies.length > 50) _S.replies.pop();
  renderReplies();
}

function renderReplies() {
  const el = $('reply-feed');
  if (!el) return;
  if (!_S.replies.length) {
    el.innerHTML = '<div style="color:var(--mute);font-size:11px;padding:12px 0">no replies yet · waiting for monitor tick</div>';
    return;
  }
  const toneMap = { reply: 'acid', bounce: 'red', unsub: 'amber' };
  el.innerHTML = _S.replies.map(r => `
    <div class="reply reply--${esc(r.kind)}">
      <div class="reply__top">
        <span class="reply__time">${esc(r.time)}</span>
        <span class="badge badge--${toneMap[r.kind] || 'default'}">${esc(r.kind)}</span>
        ${r.to ? `<span class="reply__to">→ ${esc(r.to)}</span>` : ''}
      </div>
      <div class="reply__from">${esc(r.from)}</div>
      <div class="reply__subj">${esc(r.subj)}</div>
    </div>
  `).join('');
}

// ─── Log ──────────────────────────────────────────────────────────────────────
function initLog() {
  _S.logSince = 0;
  resetLogView();
  pollLogs();
  if (_S.logTimer) clearInterval(_S.logTimer);
  _S.logTimer = setInterval(pollLogs, 2000);
}

function resetLogView() {
  const el = $('logview');
  if (!el) return;
  el.innerHTML = `<div class="logln logln--cursor">
    <span class="logln__t" id="log-cursor-time">${nowStr()}</span>
    <span class="logln__l logln__l--info">INFO</span>
    <span class="logln__src">[tty]</span>
    <span class="logln__m">waiting for events<span class="blink">_</span></span>
  </div>`;
}

async function pollLogs() {
  try {
    const data = await api(`/api/logs?since=${_S.logSince}`);
    const lines = data.logs || [];
    if (lines.length) {
      _S.logSince = data.total;
      appendLogLines(lines);
    }
  } catch {}
}

function appendLogLines(lines) {
  const el = $('logview');
  if (!el) return;
  const cursor = el.querySelector('.logln--cursor');
  lines.forEach(ln => {
    const lvl = lvlKey(ln.level);
    if (_S.logFilter !== 'all' && lvl !== _S.logFilter) return;
    const div = document.createElement('div');
    div.className = `logln logln--${lvl}`;
    const levelDisplay = ln.level === 'WARNING' ? 'WARN' : (ln.level || 'INFO').slice(0, 4);
    div.innerHTML = `<span class="logln__t">${esc(ln.time)}</span><span class="logln__l logln__l--${lvl}">${levelDisplay.padEnd(4)}</span><span class="logln__src">[app]</span><span class="logln__m">${esc(ln.message)}</span>`;
    if (cursor) el.insertBefore(div, cursor);
    else el.appendChild(div);
  });
  if (_S.autoScroll) el.scrollTop = el.scrollHeight;
}

function setLogFilter(f) {
  _S.logFilter = f;
  qsa('.log-filter__btn').forEach(b => b.classList.toggle('is-on', b.dataset.filter === f));
  _S.logSince = 0;
  resetLogView();
  pollLogs();
}

async function clearLog() {
  try {
    await api('/api/logs', { method: 'DELETE' });
    _S.logSince = 0;
    resetLogView();
  } catch {}
}

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  startClock();

  // Build 40 progress tick marks
  const ticksEl = $('progress-ticks');
  if (ticksEl) {
    ticksEl.innerHTML = Array.from({ length: 40 }).map(() => '<span class="progress__tick"></span>').join('');
  }

  // Tab click handlers
  qsa('.tab').forEach(t => t.addEventListener('click', () => navigate(t.dataset.tab)));

  // Keyboard shortcuts 1–5 (skip when typing in inputs)
  document.addEventListener('keydown', e => {
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key >= '1' && e.key <= '5') navigate(PAGES[+e.key - 1]);
  });

  // Mobile hamburger menu
  const menuToggle = $('menuToggle');
  if (menuToggle) menuToggle.addEventListener('click', () => {
    const tabs = $('tabs'); if (tabs) tabs.classList.toggle('is-open');
  });

  // Status poll every 5s
  pollStatus();
  setInterval(pollStatus, 5000);

  // Settings
  const btnSaveWh = $('btn-save-webhook'); if (btnSaveWh) btnSaveWh.addEventListener('click', saveDiscord);
  const btnTestWh = $('btn-test-webhook'); if (btnTestWh) btnTestWh.addEventListener('click', testWebhook);

  // Database file inputs
  const btnImportDb = $('btn-import-db');
  const fileImportDb = $('file-import-db');
  if (btnImportDb && fileImportDb) {
    btnImportDb.addEventListener('click', () => fileImportDb.click());
    fileImportDb.addEventListener('change', async e => {
      const file = e.target.files[0]; if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      fd.append('label', file.name.replace(/\.db$/i, ''));
      const origHtml = btnImportDb.innerHTML;
      btnImportDb.textContent = 'importing…';
      try {
        const res = await fetch('/api/imports/db', { method: 'POST', body: fd });
        const data = await res.json();
        btnImportDb.innerHTML = origHtml;
        if (data.error) { alert(data.error); } else { await loadDatabase(); showImportDetail(data.id); }
      } catch(err) { btnImportDb.innerHTML = origHtml; alert('Import failed: ' + err.message); }
      e.target.value = '';
    });
  }

  const btnImportExcel = $('btn-import-excel');
  const fileImportExcel = $('file-import-excel');
  if (btnImportExcel && fileImportExcel) {
    btnImportExcel.addEventListener('click', () => fileImportExcel.click());
    fileImportExcel.addEventListener('change', async e => {
      const file = e.target.files[0]; if (!file) return;
      const fd = new FormData();
      fd.append('file', file);
      fd.append('label', file.name.replace(/\.(xlsx|xlsm|xltx|xltm)$/i, ''));
      const origHtml = btnImportExcel.innerHTML;
      btnImportExcel.textContent = 'importing…';
      try {
        const res = await fetch('/api/imports/excel', { method: 'POST', body: fd });
        const data = await res.json();
        btnImportExcel.innerHTML = origHtml;
        if (data.error) { alert(data.error); } else { await loadDatabase(); showImportDetail(data.id); }
      } catch(err) { btnImportExcel.innerHTML = origHtml; alert('Import failed: ' + err.message); }
      e.target.value = '';
    });
  }

  const importSearch = $('import-search');
  if (importSearch) importSearch.addEventListener('input', renderImportList);

  // Outreach
  const btnSaveOut = $('btn-save-outreach'); if (btnSaveOut) btnSaveOut.addEventListener('click', saveOutreachConfig);
  const btnStartOut = $('btn-start-outreach'); if (btnStartOut) btnStartOut.addEventListener('click', toggleOutreach);

  // Monitor
  const btnSaveMon = $('btn-save-monitor'); if (btnSaveMon) btnSaveMon.addEventListener('click', saveMonitorConfig);
  const btnStartMon = $('btn-start-monitor'); if (btnStartMon) btnStartMon.addEventListener('click', toggleMonitor);

  // Log
  const btnClearLog = $('btn-clear-log'); if (btnClearLog) btnClearLog.addEventListener('click', clearLog);
  qsa('.log-filter__btn').forEach(b => b.addEventListener('click', () => setLogFilter(b.dataset.filter)));
  const chkAutoScroll = $('chk-autoscroll');
  if (chkAutoScroll) chkAutoScroll.addEventListener('change', e => { _S.autoScroll = e.target.checked; });
  const chkWrap = $('chk-wraplines');
  if (chkWrap) chkWrap.addEventListener('change', e => {
    const lv = $('logview');
    if (lv) lv.style.whiteSpace = e.target.checked ? 'pre-wrap' : '';
  });

  // Land on outreach page
  navigate('outreach');
});
