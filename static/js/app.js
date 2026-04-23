'use strict';

// ── Utilities ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const toast = (() => {
  let el = document.createElement('div');
  el.id = 'toast';
  document.body.appendChild(el);
  let timer;
  return (msg, type = 'ok') => {
    el.textContent = msg;
    el.className = `show ${type}`;
    clearTimeout(timer);
    timer = setTimeout(() => { el.className = ''; }, 3200);
  };
})();

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

async function uploadFile(path, file, label) {
  const fd = new FormData();
  fd.append('file', file);
  if (label) fd.append('label', label);
  const res = await fetch(path, { method: 'POST', body: fd });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── Tab switching ──────────────────────────────────────────────────────────────
const tabHandlers = {};
let activeTab = 'settings';

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    const tab = item.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    item.classList.add('active');
    $(`tab-${tab}`).classList.add('active');
    activeTab = tab;
    if (tabHandlers[tab]) tabHandlers[tab]();
  });
});

// ── Outreach / Monitor running state (browser-owned) ──────────────────────────
let _outreachRunning = false;
let _outreachTimer   = null;
let _monitorRunning  = false;
let _monitorTimer    = null;

function setOutreachRunning(running) {
  _outreachRunning = running;
  updateStatusBadge('status-outreach', running);
  if (activeTab === 'outreach') {
    $('btn-start-outreach').disabled = running;
    $('btn-stop-outreach').disabled  = !running;
  }
}

function setMonitorRunning(running) {
  _monitorRunning = running;
  updateStatusBadge('status-monitor', running);
  if (activeTab === 'monitor') {
    $('btn-start-monitor').disabled = running;
    $('btn-stop-monitor').disabled  = !running;
    const bar = $('monitor-status-bar');
    bar.style.display = running ? '' : 'none';
    if (running) bar.textContent = '👀 Monitor is running…';
  }
}

// ── Global status polling (badge + progress sync) ─────────────────────────────
function updateStatusBadge(id, running) {
  const el = document.querySelector(`#${id} .badge`);
  if (!el) return;
  el.className = 'badge ' + (running ? 'badge-on' : 'badge-off');
  el.textContent = running ? 'ON' : 'OFF';
}

function updateOutreachProgressUI(sentToday, limit) {
  const pct = Math.min(100, limit > 0 ? (sentToday / limit) * 100 : 0);
  $('outreach-progress-bar').style.width = pct + '%';
  $('outreach-progress-label').textContent = `${sentToday} / ${limit}`;
}

async function pollStatus() {
  try {
    const data = await api('GET', '/api/status');
    if (activeTab === 'outreach' && !_outreachRunning) {
      const sentToday = data.sent_today || 0;
      const limit     = data.daily_limit || 100;
      updateOutreachProgressUI(sentToday, limit);
    }
  } catch (_) {}
}

setInterval(pollStatus, 5000);

// Resume loops if server state says running (e.g. after page refresh)
async function initRunningState() {
  try {
    const data = await api('GET', '/api/status');
    if (data.outreach_running && !_outreachRunning) {
      setOutreachRunning(true);
      outreachLoop();
    }
    if (data.monitor_running && !_monitorRunning) {
      setMonitorRunning(true);
      monitorLoop();
    }
  } catch (_) {}
}

// ── Countdown display ─────────────────────────────────────────────────────────
let _countdownInterval = null;

function startCountdown(delayMs) {
  clearInterval(_countdownInterval);
  const end = Date.now() + delayMs;
  $('outreach-countdown').style.display = '';
  function tick() {
    const left = Math.max(0, Math.ceil((end - Date.now()) / 1000));
    $('outreach-countdown').textContent = `⏳ Next email in ${left}s…`;
    if (left === 0) clearInterval(_countdownInterval);
  }
  tick();
  _countdownInterval = setInterval(tick, 1000);
}

function stopCountdown() {
  clearInterval(_countdownInterval);
  $('outreach-countdown').style.display = 'none';
}

// ══════════════════════════════════════════════════════════════════════════════
// SETTINGS TAB
// ══════════════════════════════════════════════════════════════════════════════
let _accounts = [];
let _selectedAccountId = null;

async function loadSettings() {
  try {
    _accounts = await api('GET', '/api/accounts');
    renderAccountList();
    const discord = await api('GET', '/api/discord');
    $('discord-webhook').value = discord.webhook || '';
  } catch (e) { toast(e.message, 'err'); }
}

function renderAccountList() {
  const ul = $('account-list');
  ul.innerHTML = '';
  if (_accounts.length === 0) {
    ul.innerHTML = '<li style="color:var(--text-muted);font-size:0.78rem;padding:8px 10px">No accounts yet.</li>';
  }
  _accounts.forEach(acct => {
    const li = document.createElement('li');
    li.textContent = acct.label || acct.smtp_user || 'Account';
    li.dataset.id = acct.id;
    if (acct.id === _selectedAccountId) li.classList.add('selected');
    li.addEventListener('click', () => selectAccount(acct.id));
    ul.appendChild(li);
  });
}

function selectAccount(id) {
  _selectedAccountId = id;
  renderAccountList();
  const acct = _accounts.find(a => a.id === id);
  if (!acct) return;
  $('account-editor-empty').style.display = 'none';
  $('account-editor').style.display = '';
  $('editor-title').textContent = acct.label || 'Edit Account';
  $('editor-account-id').value       = acct.id;
  $('editor-label').value            = acct.label || '';
  $('editor-display-name').value     = acct.display_name || '';
  $('editor-smtp-host').value        = acct.smtp_host || '';
  $('editor-smtp-port').value        = acct.smtp_port || '';
  $('editor-smtp-user').value        = acct.smtp_user || '';
  $('editor-smtp-password').value    = acct.smtp_password || '';
  $('editor-imap-host').value        = acct.imap_host || '';
  $('editor-imap-port').value        = acct.imap_port || '';
  $('editor-subject').value          = acct.outreach_subject || '';
  $('editor-body').value             = acct.outreach_body || '';
}

$('btn-add-account').addEventListener('click', async () => {
  try {
    const acct = await api('POST', '/api/accounts', { label: 'New Account' });
    _accounts.push(acct);
    renderAccountList();
    selectAccount(acct.id);
    toast('Account added.');
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-remove-account').addEventListener('click', async () => {
  const id = $('editor-account-id').value;
  if (!id || !confirm('Remove this account?')) return;
  try {
    await api('DELETE', `/api/accounts/${id}`);
    _accounts = _accounts.filter(a => a.id !== id);
    _selectedAccountId = null;
    $('account-editor').style.display = 'none';
    $('account-editor-empty').style.display = '';
    renderAccountList();
    toast('Account removed.');
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-save-account').addEventListener('click', async () => {
  const id = $('editor-account-id').value;
  if (!id) return;
  const payload = {
    label:            $('editor-label').value.trim(),
    display_name:     $('editor-display-name').value.trim(),
    smtp_host:        $('editor-smtp-host').value.trim(),
    smtp_port:        $('editor-smtp-port').value.trim(),
    smtp_user:        $('editor-smtp-user').value.trim(),
    smtp_password:    $('editor-smtp-password').value,
    imap_host:        $('editor-imap-host').value.trim(),
    imap_port:        $('editor-imap-port').value.trim(),
    outreach_subject: $('editor-subject').value.trim(),
    outreach_body:    $('editor-body').value,
  };
  try {
    const updated = await api('PUT', `/api/accounts/${id}`, payload);
    const idx = _accounts.findIndex(a => a.id === id);
    if (idx >= 0) _accounts[idx] = updated;
    $('editor-title').textContent = updated.label;
    renderAccountList();
    toast('Account saved.');
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-save-discord').addEventListener('click', async () => {
  try {
    await api('POST', '/api/discord', { webhook: $('discord-webhook').value.trim() });
    toast('Discord webhook saved.');
  } catch (e) { toast(e.message, 'err'); }
});

tabHandlers['settings'] = loadSettings;

// ══════════════════════════════════════════════════════════════════════════════
// DATABASE TAB
// ══════════════════════════════════════════════════════════════════════════════
let _imports = [];
let _selectedImportId = null;

async function loadImports() {
  try {
    _imports = await api('GET', '/api/imports');
    renderImportList();
  } catch (e) { toast(e.message, 'err'); }
}

function renderImportList() {
  const ul = $('import-list');
  ul.innerHTML = '';
  if (_imports.length === 0) {
    ul.innerHTML = '<li style="color:var(--text-muted);font-size:0.78rem;padding:8px 10px">No imports yet.</li>';
    return;
  }
  _imports.forEach(imp => {
    const li = document.createElement('li');
    const date = (imp.imported_at || '').slice(0, 10);
    li.innerHTML = `<strong>#${imp.id}</strong> — ${imp.label || 'Unnamed'}<br><span style="font-size:0.7rem;color:var(--text-muted)">${date}</span>`;
    li.dataset.id = imp.id;
    if (imp.id === _selectedImportId) li.classList.add('selected');
    li.addEventListener('click', () => selectImport(imp.id));
    ul.appendChild(li);
  });
}

function selectImport(id) {
  _selectedImportId = id;
  renderImportList();
  const imp = _imports.find(i => i.id === id);
  if (!imp) return;
  $('db-stats-empty').style.display = 'none';
  $('db-stats').style.display = '';
  $('stats-label').textContent = imp.label || `Import #${id}`;
  const s = imp.stats || {};
  $('stat-total').textContent   = s.total   ?? '—';
  $('stat-sent').textContent    = s.sent    ?? '—';
  $('stat-replied').textContent = s.replied ?? '—';
  $('stat-left').textContent    = s.left    ?? '—';
  $('locations-panel').style.display = 'none';
  $('locations-list').innerHTML = '';
}

$('btn-import-db').addEventListener('click', () => $('file-input-db').click());
$('btn-import-excel').addEventListener('click', () => $('file-input-excel').click());

$('file-input-db').addEventListener('change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  const label = prompt('Label for this import:', file.name.replace(/\.db$/i, ''));
  if (label === null) return;
  try {
    toast('Importing…');
    const result = await uploadFile('/api/imports/db', file, label || file.name);
    toast(`Imported ${result.stats?.total ?? 0} leads.`);
    await loadImports();
    selectImport(result.id);
  } catch (e) { toast(e.message, 'err'); }
  e.target.value = '';
});

$('file-input-excel').addEventListener('change', async e => {
  const files = Array.from(e.target.files);
  if (!files.length) return;
  e.target.value = '';

  let succeeded = 0;
  let lastId = null;
  const errors = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const label = file.name.replace(/\.[^.]+$/, '');
    toast(`Importing ${i + 1} / ${files.length}: ${label}…`);
    try {
      const result = await uploadFile('/api/imports/excel', file, label);
      succeeded++;
      lastId = result.id;
    } catch (err) {
      errors.push(`${label}: ${err.message}`);
    }
  }

  await loadImports();
  if (lastId) selectImport(lastId);

  if (errors.length) {
    toast(`${succeeded}/${files.length} imported. Errors: ${errors.join(' | ')}`, 'err');
  } else {
    toast(`${succeeded} file${succeeded !== 1 ? 's' : ''} imported successfully.`);
  }
});

$('btn-remove-import').addEventListener('click', async () => {
  if (!_selectedImportId || !confirm('Delete this import and its working database?')) return;
  try {
    await api('DELETE', `/api/imports/${_selectedImportId}`);
    _selectedImportId = null;
    $('db-stats').style.display = 'none';
    $('db-stats-empty').style.display = '';
    toast('Import removed.');
    await loadImports();
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-refresh-imports').addEventListener('click', async () => {
  try {
    const r = await api('POST', '/api/imports/refresh');
    toast(`Refreshed ${r.count} import(s).`);
    await loadImports();
    if (_selectedImportId) selectImport(_selectedImportId);
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-load-locations').addEventListener('click', async () => {
  if (!_selectedImportId) return;
  const btn = $('btn-load-locations');
  btn.disabled = true;
  btn.textContent = '⏳ Resolving…';
  try {
    const data = await api('GET', `/api/imports/${_selectedImportId}/locations`);
    renderLocations(data.locations || []);
    $('locations-panel').style.display = '';
  } catch (e) { toast(e.message, 'err'); }
  btn.disabled = false;
  btn.textContent = '🌍 Load Location Breakdown';
});

function renderLocations(locations) {
  const el = $('locations-list');
  el.innerHTML = '';
  if (!locations.length) {
    el.innerHTML = '<p style="color:var(--text-muted);font-size:0.8rem;padding:8px 0">No phone numbers to resolve.</p>';
    return;
  }
  const max = locations[0].count || 1;
  locations.forEach(loc => {
    const pct = Math.round((loc.count / max) * 100);
    const row = document.createElement('div');
    row.className = 'location-row';
    row.style.flexDirection = 'column';
    row.style.alignItems = 'stretch';
    row.innerHTML = `
      <div style="display:flex;justify-content:space-between">
        <span>${loc.country}</span>
        <span style="color:var(--accent)">${loc.count.toLocaleString()}</span>
      </div>
      <div class="location-bar" style="width:${pct}%"></div>
    `;
    el.appendChild(row);
  });
}

tabHandlers['database'] = loadImports;

// ══════════════════════════════════════════════════════════════════════════════
// OUTREACH TAB
// ══════════════════════════════════════════════════════════════════════════════
let _outreachConfig = null;

async function loadOutreachConfig() {
  try {
    _outreachConfig = await api('GET', '/api/outreach/config');
    renderOutreachAccounts(_outreachConfig.accounts || []);
    renderImportDropdown(_outreachConfig.imports || []);
    $('outreach-limit').value     = _outreachConfig.daily_limit ?? 100;
    $('outreach-delay-min').value = _outreachConfig.delay_min   ?? 120;
    $('outreach-delay-max').value = _outreachConfig.delay_max   ?? 300;
    $('outreach-simultaneous').checked = !!_outreachConfig.send_simultaneously;
    if (_outreachConfig.working_path) {
      const imp = (_outreachConfig.imports || []).find(i => i.working_path === _outreachConfig.working_path);
      if (imp) $('outreach-db-select').value = imp.working_path;
    }
  } catch (e) { toast(e.message, 'err'); }
  $('btn-start-outreach').disabled = _outreachRunning;
  $('btn-stop-outreach').disabled  = !_outreachRunning;
  loadOutreachHistory();
}

function renderImportDropdown(imports) {
  const sel = $('outreach-db-select');
  const prev = sel.value;
  sel.innerHTML = '<option value="">— Select import —</option>';
  imports.forEach(imp => {
    const opt = document.createElement('option');
    opt.value = imp.working_path;
    opt.textContent = `#${imp.id} — ${imp.label || 'Unnamed'}`;
    sel.appendChild(opt);
  });
  if (prev) sel.value = prev;
}

function renderOutreachAccounts(accounts) {
  const tbl = $('sender-mix-table');
  tbl.innerHTML = '';
  if (!accounts.length) {
    tbl.innerHTML = '<p style="color:var(--text-muted);font-size:0.8rem">No email accounts configured.</p>';
    return;
  }
  accounts.forEach(acct => {
    const row = document.createElement('div');
    row.className = 'sender-mix-row';
    row.innerHTML = `
      <span class="sender-name" title="${acct.smtp_user || ''}">${acct.label || acct.smtp_user || 'Account'}</span>
      <input type="number" min="0" max="100" value="${acct.weight ?? 0}" data-id="${acct.id}" style="width:70px;text-align:center">
      <span style="color:var(--text-muted);font-size:0.7rem">%</span>
    `;
    tbl.appendChild(row);
  });
}

function getSenderMixFromUI(accounts) {
  return (accounts || []).map(acct => {
    const input = document.querySelector(`#sender-mix-table input[data-id="${acct.id}"]`);
    return { ...acct, weight: input ? parseInt(input.value) || 0 : acct.weight || 0 };
  });
}

$('btn-save-outreach').addEventListener('click', async () => {
  if (!_outreachConfig) return;
  const accountsWithWeights = getSenderMixFromUI(_outreachConfig.accounts);
  const payload = {
    working_path:        $('outreach-db-select').value,
    daily_limit:         parseInt($('outreach-limit').value)     || 100,
    delay_min:           parseInt($('outreach-delay-min').value) || 120,
    delay_max:           parseInt($('outreach-delay-max').value) || 300,
    send_simultaneously: $('outreach-simultaneous').checked,
    accounts:            accountsWithWeights,
  };
  try {
    await api('POST', '/api/outreach/config', payload);
    toast('Outreach config saved.');
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-start-outreach').addEventListener('click', async () => {
  if (!_outreachConfig) return;
  const dbPath = $('outreach-db-select').value;
  if (!dbPath) { toast('Select a database first.', 'err'); return; }
  const accountsWithWeights = getSenderMixFromUI(_outreachConfig.accounts);
  const senderMix = {};
  accountsWithWeights.forEach(a => { senderMix[a.id] = a.weight; });
  const payload = {
    working_path:        dbPath,
    daily_limit:         parseInt($('outreach-limit').value)     || 100,
    delay_min:           parseInt($('outreach-delay-min').value) || 120,
    delay_max:           parseInt($('outreach-delay-max').value) || 300,
    send_simultaneously: $('outreach-simultaneous').checked,
    sender_mix:          senderMix,
  };
  try {
    await api('POST', '/api/outreach/start', payload);
    setOutreachRunning(true);
    toast('Outreach started.');
    outreachLoop();
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-stop-outreach').addEventListener('click', async () => {
  clearTimeout(_outreachTimer);
  stopCountdown();
  setOutreachRunning(false);
  try {
    await api('POST', '/api/outreach/stop');
  } catch (_) {}
  toast('Outreach stopped.');
  loadOutreachHistory();
});

async function outreachLoop() {
  if (!_outreachRunning) return;
  try {
    const result = await api('POST', '/api/outreach/tick');
    if (!result.ok) {
      setOutreachRunning(false);
      stopCountdown();
      if (result.reason === 'done' || result.done) {
        toast('Outreach complete — daily limit reached.');
      }
      loadOutreachHistory();
      return;
    }
    // Update progress bar
    updateOutreachProgressUI(result.sent_today || 0, result.daily_limit || parseInt($('outreach-limit').value) || 100);

    if (result.done) {
      setOutreachRunning(false);
      stopCountdown();
      toast('Outreach complete — daily limit reached.');
      loadOutreachHistory();
      return;
    }

    // Schedule next tick with randomised delay
    const delayMin = result.delay_min || parseInt($('outreach-delay-min').value) || 120;
    const delayMax = result.delay_max || parseInt($('outreach-delay-max').value) || 300;
    const delayMs  = (delayMin + Math.random() * (delayMax - delayMin)) * 1000;
    startCountdown(delayMs);
    _outreachTimer = setTimeout(outreachLoop, delayMs);
  } catch (e) {
    // Retry after 15s on network/server error
    _outreachTimer = setTimeout(outreachLoop, 15000);
  }
}

$('btn-reset-history').addEventListener('click', async () => {
  if (!confirm("Reset today's outreach history?")) return;
  try {
    await api('POST', '/api/outreach/history/reset');
    toast("Today's history reset.");
    loadOutreachHistory();
  } catch (e) { toast(e.message, 'err'); }
});

async function loadOutreachHistory() {
  try {
    const days = await api('GET', '/api/outreach/history');
    renderOutreachHistory(days);
    const today = days.find(d => d.date === todayKey());
    if (today) {
      const sent  = today.total_sent || 0;
      const limit = parseInt($('outreach-limit').value) || 100;
      const totalAll = days.reduce((sum, d) => sum + (d.total_sent || 0), 0);
      $('outreach-summary').innerHTML =
        `Today <strong style="color:var(--accent)">(${today.date})</strong>: ` +
        `<strong>${sent}</strong> / ${limit} emails sent. ` +
        `<strong>${Math.max(0, limit - sent)}</strong> remaining today. ` +
        `<span style="color:var(--text-muted)">(${totalAll} total in last 3 days)</span>`;
      updateOutreachProgressUI(sent, limit);
    } else {
      $('outreach-summary').textContent = 'No emails sent today.';
    }
  } catch (e) {}
}

function renderOutreachHistory(days) {
  const container = $('history-container');
  container.innerHTML = '';
  if (!days || !days.length) {
    container.innerHTML = '<p style="color:var(--text-muted);font-size:0.8rem;padding:12px 0">No history yet.</p>';
    return;
  }
  days.forEach(day => {
    if (!day.entries || !day.entries.length) return;
    const div = document.createElement('div');
    div.className = 'history-day';
    div.innerHTML = `<div class="history-day-header">${day.date} — ${day.total_sent} sent</div>`;
    const bySender = {};
    day.entries.forEach(e => {
      const key = e.sender_label || e.sender_email || 'Unknown';
      if (!bySender[key]) bySender[key] = [];
      bySender[key].push(e);
    });
    Object.entries(bySender).forEach(([sender, entries]) => {
      const senderDiv = document.createElement('div');
      senderDiv.style.marginBottom = '8px';
      senderDiv.innerHTML = `<div style="font-size:0.7rem;color:var(--text-muted);margin:6px 0 4px;letter-spacing:0.5px">via ${sender} (${entries.length})</div>`;
      entries.slice(0, 50).forEach(entry => {
        const row = document.createElement('div');
        row.className = 'history-entry';
        const ts = (entry.timestamp || '').slice(11, 19);
        row.innerHTML = `
          <span class="history-time">${ts}</span>
          <span class="history-email">${entry.name || ''} &lt;${entry.email || ''}&gt;</span>
          <span class="history-location">${entry.location || ''}</span>
          <span class="history-sender">${entry.sender_email || ''}</span>
        `;
        senderDiv.appendChild(row);
      });
      if (entries.length > 50) {
        senderDiv.innerHTML += `<p style="color:var(--text-muted);font-size:0.72rem;padding:4px 0">… and ${entries.length - 50} more</p>`;
      }
      div.appendChild(senderDiv);
    });
    container.appendChild(div);
  });
}

function todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

tabHandlers['outreach'] = loadOutreachConfig;

// ══════════════════════════════════════════════════════════════════════════════
// MONITOR TAB
// ══════════════════════════════════════════════════════════════════════════════
let _monitorConfig = null;

async function loadMonitorConfig() {
  try {
    _monitorConfig = await api('GET', '/api/monitor/config');
    $('monitor-interval').value = _monitorConfig.check_interval ?? 120;
    renderMonitorAccounts(_monitorConfig.accounts || [], _monitorConfig.account_ids || []);
  } catch (e) { toast(e.message, 'err'); }
  $('btn-start-monitor').disabled = _monitorRunning;
  $('btn-stop-monitor').disabled  = !_monitorRunning;
  const bar = $('monitor-status-bar');
  bar.style.display = _monitorRunning ? '' : 'none';
  if (_monitorRunning) bar.textContent = '👀 Monitor is running…';
}

function renderMonitorAccounts(accounts, selectedIds) {
  const container = $('monitor-accounts');
  container.innerHTML = '';
  if (!accounts.length) {
    container.innerHTML = '<p style="color:var(--text-muted);font-size:0.8rem">No email accounts configured.</p>';
    return;
  }
  accounts.forEach(acct => {
    const label = document.createElement('label');
    label.className = 'checkbox-label';
    const checked = selectedIds.includes(acct.id) ? 'checked' : '';
    label.innerHTML = `<input type="checkbox" value="${acct.id}" ${checked}> ${acct.label || acct.smtp_user || 'Account'}`;
    container.appendChild(label);
  });
}

$('btn-save-monitor').addEventListener('click', async () => {
  const accountIds = Array.from(document.querySelectorAll('#monitor-accounts input:checked')).map(c => c.value);
  try {
    await api('POST', '/api/monitor/config', {
      check_interval: parseInt($('monitor-interval').value) || 120,
      account_ids: accountIds,
    });
    toast('Monitor config saved.');
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-start-monitor').addEventListener('click', async () => {
  const accountIds = Array.from(document.querySelectorAll('#monitor-accounts input:checked')).map(c => c.value);
  if (!accountIds.length) { toast('Select at least one inbox account.', 'err'); return; }
  try {
    await api('POST', '/api/monitor/start', {
      check_interval: parseInt($('monitor-interval').value) || 120,
      account_ids: accountIds,
    });
    setMonitorRunning(true);
    toast('Monitor started.');
    monitorLoop();
  } catch (e) { toast(e.message, 'err'); }
});

$('btn-stop-monitor').addEventListener('click', async () => {
  clearTimeout(_monitorTimer);
  setMonitorRunning(false);
  try {
    await api('POST', '/api/monitor/stop');
  } catch (_) {}
  toast('Monitor stopped.');
});

async function monitorLoop() {
  if (!_monitorRunning) return;
  try {
    const result = await api('POST', '/api/monitor/tick');
    if (!result.ok) {
      setMonitorRunning(false);
      return;
    }
    const interval = (result.check_interval || parseInt($('monitor-interval').value) || 120) * 1000;
    _monitorTimer = setTimeout(monitorLoop, interval);
  } catch (e) {
    // Retry after 30s on error
    _monitorTimer = setTimeout(monitorLoop, 30000);
  }
}

tabHandlers['monitor'] = loadMonitorConfig;

// ══════════════════════════════════════════════════════════════════════════════
// LOGS TAB
// ══════════════════════════════════════════════════════════════════════════════
let _logAutoScroll = true;
let _logTotal      = 0;
let _logPollTimer  = null;

function appendLogLine(entry) {
  const el = $('log-output');
  const line = document.createElement('div');
  line.className = `log-line ${entry.level || ''}`;
  line.innerHTML = `<span class="log-time">[${entry.time || ''}]</span><span class="log-msg">${escHtml(entry.message || '')}</span>`;
  el.appendChild(line);
  // Keep at most 600 lines
  while (el.childElementCount > 600) el.removeChild(el.firstChild);
  if (_logAutoScroll) el.scrollTop = el.scrollHeight;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function pollLogs() {
  try {
    const data = await api('GET', `/api/logs?since=${_logTotal}`);
    const newLogs = data.logs || [];
    newLogs.forEach(appendLogLine);
    _logTotal = data.total ?? (_logTotal + newLogs.length);
  } catch (_) {}
  _logPollTimer = setTimeout(pollLogs, 1500);
}

function startLogPolling() {
  clearTimeout(_logPollTimer);
  pollLogs();
}

$('btn-clear-logs').addEventListener('click', async () => {
  try {
    await api('DELETE', '/api/logs');
    $('log-output').innerHTML = '';
    _logTotal = 0;
  } catch (e) { toast(e.message, 'err'); }
});

$('log-output').addEventListener('scroll', () => {
  const el = $('log-output');
  _logAutoScroll = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
});

tabHandlers['logs'] = () => {};

// ── Init ───────────────────────────────────────────────────────────────────────
loadSettings();
startLogPolling();
initRunningState();
