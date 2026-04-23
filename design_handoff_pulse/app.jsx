const { useState, useEffect, useRef, useMemo } = React;

// ============ DATA ============
const SEED_IMPORTS = [
  { id: 69, name: "TL2", date: "2026-04-23", leads: 1240, sent: 0, replied: 0 },
  { id: 68, name: "Tip Canada", date: "2026-04-23", leads: 890, sent: 340, replied: 12 },
  { id: 67, name: "ROSE M", date: "2026-04-23", leads: 2100, sent: 2100, replied: 44 },
  { id: 66, name: "ROSE", date: "2026-04-22", leads: 1780, sent: 1780, replied: 38 },
  { id: 65, name: "Rose EXTRA12", date: "2026-04-22", leads: 650, sent: 0, replied: 0 },
  { id: 64, name: "ROSE BB", date: "2026-04-22", leads: 920, sent: 920, replied: 17 },
  { id: 63, name: "R-Michael_1671550636", date: "2026-04-21", leads: 3200, sent: 1800, replied: 29 },
  { id: 62, name: "Replacement Rose", date: "2026-04-21", leads: 450, sent: 450, replied: 8 },
  { id: 61, name: "Replacement MAX", date: "2026-04-21", leads: 780, sent: 780, replied: 22 },
  { id: 60, name: "Replacement MAX (1)", date: "2026-04-20", leads: 330, sent: 330, replied: 4 },
  { id: 59, name: "Replacement Marcus", date: "2026-04-20", leads: 1100, sent: 1100, replied: 31 },
  { id: 58, name: "1000 CA depositors 6.3.2024", date: "2026-04-20", leads: 1000, sent: 858, replied: 19 },
];

const COUNTRIES = ["SE", "NO", "Belgium", "Canada", "AT", "Germany", "Poland", "DK", "FI", "NL"];
const LAST_NAMES = ["Gullberg","Elefsiljonn","Gul","Gullaksen","Clouter","Auger","Fuchs","Guthoff","Klimek","Kamerknorre","Selvaratnam","Schaffara","Gruber","Hemeon","Grobacher","Lindström","Vandenberg","Martin","Novak","Koch"];
const DOMAINS = ["gmail.com","yahoo.com","hotmail.com","gmx.de","msn.com","outlook.com","proton.me","web.de","t-online.de","live.com"];

const now = new Date();
const pad = (n) => String(n).padStart(2, "0");
const timeStr = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

function genActivity(n = 60) {
  const rows = [];
  const base = new Date();
  base.setHours(9, 0, 0, 0);
  for (let i = 0; i < n; i++) {
    const t = new Date(base.getTime() + i * 1000 * 27 + Math.random() * 5000);
    const ln = LAST_NAMES[Math.floor(Math.random() * LAST_NAMES.length)].toLowerCase();
    const first = ["gunnar","kristian","guillaume","werner","ernst","max","peter","jan","lukas","oli"][Math.floor(Math.random()*10)];
    const dom = DOMAINS[Math.floor(Math.random() * DOMAINS.length)];
    rows.push({
      time: timeStr(t),
      name: LAST_NAMES[Math.floor(Math.random() * LAST_NAMES.length)],
      email: `${first}${Math.floor(Math.random()*9999)}@${dom}`,
      country: COUNTRIES[Math.floor(Math.random() * COUNTRIES.length)],
      sender: Math.random() > 0.5 ? "brianteller" : "alexmartin",
    });
  }
  return rows;
}

// ============ PRIMITIVES ============
const Kbd = ({ children }) => <span className="kbd">{children}</span>;

const Dot = ({ state = "off" }) => {
  const color = { on: "var(--acid)", off: "var(--mute)", warn: "var(--amber)", err: "var(--red)" }[state];
  return <span className="dot" style={{ background: color, boxShadow: state === "on" ? `0 0 8px ${color}` : "none" }} />;
};

const AsciiRule = ({ label, char = "─" }) => (
  <div className="ascii-rule">
    {label && <span className="ascii-rule__label">{label}</span>}
    <span className="ascii-rule__line">{char.repeat(200)}</span>
  </div>
);

const Badge = ({ children, tone = "default" }) => (
  <span className={`badge badge--${tone}`}>{children}</span>
);

const Btn = ({ children, onClick, tone = "default", disabled, icon }) => (
  <button className={`btn btn--${tone}`} onClick={onClick} disabled={disabled}>
    {icon && <span className="btn__icon">{icon}</span>}
    {children}
  </button>
);

// ============ LAYOUT ============
function TopBar({ page, setPage, outreachOn, monitorOn }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const pages = [
    { id: "settings", label: "settings" },
    { id: "database", label: "database" },
    { id: "outreach", label: "outreach" },
    { id: "monitor", label: "monitor" },
    { id: "log", label: "log" },
  ];
  const go = (id) => { setPage(id); setMenuOpen(false); };
  return (
    <header className="topbar">
      <div className="topbar__left">
        <div className="brand">
          <img src="assets/favicon.png" alt="pulse" className="brand__mark" />
          <span className="brand__name">pulse</span>
          <span className="brand__version">v2.1.0</span>
          <button
            className="menu-toggle"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="toggle menu"
          >
            {menuOpen ? "×" : "≡"} <span style={{fontSize:11}}>{page}</span>
          </button>
        </div>
        <nav className={`tabs ${menuOpen ? "is-open" : ""}`}>
          {pages.map((p) => (
            <button
              key={p.id}
              className={`tab ${page === p.id ? "is-active" : ""}`}
              onClick={() => go(p.id)}
            >
              <span className="tab__bracket">[</span>
              <span className="tab__label">{p.label}</span>
              <span className="tab__bracket">]</span>
            </button>
          ))}
        </nav>
      </div>
      <div className="topbar__right">
        <div className="sys-stat">
          <Dot state={outreachOn ? "on" : "off"} />
          <span>outreach</span>
          <code>{outreachOn ? "running" : "idle"}</code>
        </div>
        <div className="sys-stat">
          <Dot state={monitorOn ? "on" : "off"} />
          <span>monitor</span>
          <code>{monitorOn ? "running" : "idle"}</code>
        </div>
        <div className="sys-user">
          <span className="sys-user__mail">user@pulse</span>
          <button className="sys-user__out">sign out ↗</button>
        </div>
      </div>
    </header>
  );
}

function StatusBar({ page, outreachOn, monitorOn }) {
  const [clock, setClock] = useState(timeStr(new Date()));
  useEffect(() => {
    const i = setInterval(() => setClock(timeStr(new Date())), 1000);
    return () => clearInterval(i);
  }, []);
  return (
    <footer className="statusbar">
      <div className="statusbar__left">
        <span>~/pulse</span>
        <span className="statusbar__sep">/</span>
        <span className="statusbar__page">{page}</span>
      </div>
      <div className="statusbar__mid">
        <span>2 accounts linked</span>
        <span className="statusbar__sep">·</span>
        <span>webhook ok</span>
        <span className="statusbar__sep">·</span>
        <span>smtp: <b className="acid">ok</b></span>
        <span className="statusbar__sep">·</span>
        <span>imap: <b className="acid">ok</b></span>
      </div>
      <div className="statusbar__right">
        <Kbd>?</Kbd> help
        <Kbd>⌘K</Kbd> cmd
        <span className="statusbar__clock">{clock}</span>
      </div>
    </footer>
  );
}

// ============ PAGES ============
function Settings() {
  const [accounts, setAccounts] = useState([
    { id: 1, email: "brianteller@blockchain-ltd-communications.com", daily: 500, sent: 431, active: true, warmup: 87 },
    { id: 2, email: "alexmartin@blockchain-ltd-communications.com", daily: 500, sent: 427, active: true, warmup: 92 },
  ]);
  const [selected, setSelected] = useState(1);
  const [webhook, setWebhook] = useState("https://discord.com/api/webhooks/1234567890/abcXYZ...");
  const acct = accounts.find((a) => a.id === selected);

  return (
    <div className="page page--settings">
      <PageHeader
        title="settings"
        cmd="pulse config edit"
        desc="accounts, routing, webhooks. the knobs that don't change often."
      />

      <div className="settings-grid">
        <section className="panel">
          <div className="panel__head">
            <span className="panel__num">01</span>
            <h3>email accounts</h3>
            <span className="panel__meta">{accounts.length} / 10</span>
          </div>
          <div className="acct-list">
            {accounts.map((a) => (
              <button
                key={a.id}
                className={`acct ${selected === a.id ? "is-sel" : ""}`}
                onClick={() => setSelected(a.id)}
              >
                <div className="acct__row">
                  <Dot state={a.active ? "on" : "off"} />
                  <span className="acct__email">{a.email}</span>
                </div>
                <div className="acct__meta">
                  <span>{a.sent}/{a.daily} today</span>
                  <span className="acct__sep">·</span>
                  <span>warmup {a.warmup}%</span>
                </div>
              </button>
            ))}
            <button className="acct acct--add">
              <span>+ add account</span>
              <span className="acct__hint">smtp / imap / oauth</span>
            </button>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <span className="panel__num">02</span>
            <h3>account detail</h3>
            <span className="panel__meta">{acct?.email}</span>
          </div>
          <div className="detail">
            <Field label="smtp host" value="smtp.blockchain-ltd-communications.com" />
            <Field label="smtp port" value="587" />
            <Field label="imap host" value="imap.blockchain-ltd-communications.com" />
            <Field label="imap port" value="993" />
            <Field label="from name" value="Brian Teller" />
            <Field label="daily limit" value={acct?.daily} small />
            <div className="detail__row">
              <span className="detail__label">warmup</span>
              <div className="bar">
                <div className="bar__fill" style={{ width: `${acct?.warmup}%` }} />
                <span className="bar__val">{acct?.warmup}%</span>
              </div>
            </div>
            <div className="detail__actions">
              <Btn tone="primary">save</Btn>
              <Btn>test connection</Btn>
              <Btn tone="danger">remove</Btn>
            </div>
          </div>
        </section>

        <section className="panel panel--wide">
          <div className="panel__head">
            <span className="panel__num">03</span>
            <h3>discord webhook</h3>
            <span className="panel__meta">notifications for replies</span>
          </div>
          <div className="webhook">
            <code className="webhook__url">{webhook}</code>
            <div className="webhook__actions">
              <Btn>test ping</Btn>
              <Btn tone="primary">save</Btn>
            </div>
          </div>
          <div className="webhook__events">
            <label><input type="checkbox" defaultChecked /> on reply</label>
            <label><input type="checkbox" defaultChecked /> on bounce</label>
            <label><input type="checkbox" /> on send (verbose)</label>
            <label><input type="checkbox" defaultChecked /> on daily summary</label>
          </div>
        </section>
      </div>
    </div>
  );
}

function Field({ label, value, small }) {
  return (
    <div className={`field ${small ? "field--small" : ""}`}>
      <label>{label}</label>
      <input defaultValue={value} />
    </div>
  );
}

function PageHeader({ title, cmd, desc, right }) {
  return (
    <div className="pagehead">
      <div className="pagehead__main">
        <div className="pagehead__crumb">
          <span>$</span> <code>{cmd}</code>
        </div>
        <h1 className="pagehead__title">{title}</h1>
        <p className="pagehead__desc">{desc}</p>
      </div>
      {right && <div className="pagehead__right">{right}</div>}
    </div>
  );
}

function Database() {
  const [selected, setSelected] = useState(58);
  const [query, setQuery] = useState("");
  const list = SEED_IMPORTS.filter((i) =>
    i.name.toLowerCase().includes(query.toLowerCase())
  );
  const active = SEED_IMPORTS.find((i) => i.id === selected);
  const left = active ? Math.max(0, active.leads - active.sent) : 0;

  return (
    <div className="page page--database">
      <PageHeader
        title="database"
        cmd="pulse db ls"
        desc="imported lead lists. select one to inspect or push into outreach."
        right={
          <div className="row-btns">
            <Btn icon="+">import .db</Btn>
            <Btn icon="▦">import excel</Btn>
            <Btn icon="↻" tone="primary">refresh</Btn>
          </div>
        }
      />

      <div className="db-grid">
        <section className="panel panel--list">
          <div className="panel__head">
            <span className="panel__num">01</span>
            <h3>imports</h3>
            <span className="panel__meta">{list.length} lists</span>
          </div>
          <div className="search">
            <span className="search__prefix">/</span>
            <input
              placeholder="filter imports…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="imports">
            {list.map((imp) => {
              const pct = Math.round((imp.sent / imp.leads) * 100);
              return (
                <button
                  key={imp.id}
                  className={`imp ${selected === imp.id ? "is-sel" : ""}`}
                  onClick={() => setSelected(imp.id)}
                >
                  <div className="imp__top">
                    <span className="imp__id">#{imp.id}</span>
                    <span className="imp__name">{imp.name}</span>
                    <span className="imp__leads">{imp.leads.toLocaleString()}</span>
                  </div>
                  <div className="imp__bot">
                    <span className="imp__date">{imp.date}</span>
                    <div className="imp__bar">
                      <div className="imp__fill" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="imp__pct">{pct}%</span>
                  </div>
                </button>
              );
            })}
          </div>
        </section>

        <section className="panel panel--detail">
          <div className="panel__head">
            <span className="panel__num">02</span>
            <h3>{active?.name || "—"}.xlsx</h3>
            <span className="panel__meta">id #{active?.id} · {active?.date}</span>
            <div className="panel__actions">
              <Btn>export csv</Btn>
              <Btn tone="danger">delete</Btn>
            </div>
          </div>

          <div className="stat-grid">
            <Stat label="leads with email" value={active?.leads.toLocaleString()} />
            <Stat label="sent" value={active?.sent.toLocaleString()} tone="acid" />
            <Stat label="replied" value={active?.replied} tone="amber" />
            <Stat label="left / not sent" value={left.toLocaleString()} />
          </div>

          <AsciiRule label="location breakdown" />

          <div className="loc-breakdown">
            {[
              { c: "Germany", n: 312, p: 31 },
              { c: "Canada", n: 187, p: 19 },
              { c: "Sweden", n: 124, p: 12 },
              { c: "Norway", n: 98, p: 10 },
              { c: "Austria", n: 76, p: 8 },
              { c: "Belgium", n: 62, p: 6 },
              { c: "Netherlands", n: 58, p: 6 },
              { c: "Denmark", n: 41, p: 4 },
              { c: "Finland", n: 28, p: 3 },
              { c: "Other", n: 14, p: 1 },
            ].map((row) => (
              <div className="loc" key={row.c}>
                <span className="loc__c">{row.c}</span>
                <div className="loc__track">
                  <div className="loc__fill" style={{ width: `${row.p * 3}%` }} />
                </div>
                <span className="loc__n">{row.n}</span>
                <span className="loc__pct">{row.p}%</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value, tone = "default" }) {
  return (
    <div className={`stat stat--${tone}`}>
      <div className="stat__val">{value}</div>
      <div className="stat__label">{label}</div>
      <div className="stat__corner">┐</div>
    </div>
  );
}

function Outreach({ outreachOn, setOutreachOn }) {
  const [db, setDb] = useState("#58 — 1000 CA depositors 6.3.2024");
  const [weights, setWeights] = useState({ brian: 50, alex: 50 });
  const [dailyLimit, setDailyLimit] = useState(1000);
  const [minDelay, setMinDelay] = useState(30);
  const [maxDelay, setMaxDelay] = useState(50);
  const [sim, setSim] = useState(false);
  const [sent, setSent] = useState(858);
  const [activity, setActivity] = useState(() => genActivity(40));
  const target = 1000;

  useEffect(() => {
    if (!outreachOn) return;
    const i = setInterval(() => {
      setSent((s) => Math.min(target, s + 1));
      setActivity((a) => {
        const d = new Date();
        const ln = LAST_NAMES[Math.floor(Math.random() * LAST_NAMES.length)];
        const first = ln.toLowerCase();
        const dom = DOMAINS[Math.floor(Math.random() * DOMAINS.length)];
        const next = {
          time: timeStr(d),
          name: ln,
          email: `${first}${Math.floor(Math.random()*9999)}@${dom}`,
          country: COUNTRIES[Math.floor(Math.random() * COUNTRIES.length)],
          sender: Math.random() > 0.5 ? "brianteller" : "alexmartin",
        };
        return [next, ...a].slice(0, 60);
      });
    }, 1400);
    return () => clearInterval(i);
  }, [outreachOn]);

  const pct = Math.round((sent / target) * 100);

  return (
    <div className="page page--outreach">
      <PageHeader
        title="outreach"
        cmd="pulse outreach run"
        desc="dispatch sends across the connected accounts at human-ish cadence."
      />

      <div className="out-grid">
        <section className="panel">
          <div className="panel__head">
            <span className="panel__num">01</span>
            <h3>list selection</h3>
          </div>
          <div className="detail">
            <div className="field">
              <label>active database</label>
              <select value={db} onChange={(e) => setDb(e.target.value)}>
                {SEED_IMPORTS.map((i) => (
                  <option key={i.id}>#{i.id} — {i.name}</option>
                ))}
              </select>
            </div>
            <div className="note">
              <span className="note__tag">note</span>
              only leads with an email and not already sent are queued.
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <span className="panel__num">02</span>
            <h3>sender weights</h3>
            <span className="panel__meta">auto-normalized</span>
          </div>
          <div className="weights">
            <Weight
              email="brianteller@blockchain-ltd-communications.com"
              value={weights.brian}
              onChange={(v) => setWeights({ ...weights, brian: v })}
            />
            <Weight
              email="alexmartin@blockchain-ltd-communications.com"
              value={weights.alex}
              onChange={(v) => setWeights({ ...weights, alex: v })}
            />
            <div className="weights__hint">set to 0 to exclude an account.</div>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <span className="panel__num">03</span>
            <h3>sending parameters</h3>
          </div>
          <div className="params">
            <div className="field">
              <label>daily send limit</label>
              <input type="number" value={dailyLimit} onChange={(e) => setDailyLimit(+e.target.value)} />
            </div>
            <div className="field-row">
              <div className="field">
                <label>min delay (s)</label>
                <input type="number" value={minDelay} onChange={(e) => setMinDelay(+e.target.value)} />
              </div>
              <div className="field">
                <label>max delay (s)</label>
                <input type="number" value={maxDelay} onChange={(e) => setMaxDelay(+e.target.value)} />
              </div>
            </div>
            <label className="check">
              <input type="checkbox" checked={sim} onChange={(e) => setSim(e.target.checked)} />
              <span>send simultaneously across accounts</span>
            </label>
            <div className="actions-row">
              <Btn tone="primary">save config</Btn>
              <Btn
                tone={outreachOn ? "danger" : "acid"}
                onClick={() => setOutreachOn(!outreachOn)}
              >
                {outreachOn ? "◼ stop" : "▶ start outreach"}
              </Btn>
            </div>
          </div>
        </section>

        <section className="panel panel--progress">
          <div className="panel__head">
            <span className="panel__num">04</span>
            <h3>progress</h3>
            <span className="panel__meta">today</span>
          </div>
          <div className="progress">
            <div className="progress__row">
              <span className="progress__big">{sent.toLocaleString()}</span>
              <span className="progress__slash">/</span>
              <span className="progress__target">{target.toLocaleString()}</span>
              <span className="progress__pct">{pct}%</span>
            </div>
            <div className="progress__bar">
              <div className="progress__fill" style={{ width: `${pct}%` }} />
              <div className="progress__ticks">
                {Array.from({ length: 40 }).map((_, i) => (
                  <span key={i} className={`progress__tick ${i / 40 < pct / 100 ? "on" : ""}`} />
                ))}
              </div>
            </div>
            <div className="progress__meta">
              <span>eta ~{Math.max(0, Math.round((target - sent) * 0.7))}m</span>
              <span>avg {((minDelay + maxDelay) / 2).toFixed(0)}s/send</span>
              <span className="acid">{outreachOn ? "● running" : "◌ idle"}</span>
            </div>
          </div>
        </section>

        <section className="panel panel--activity">
          <div className="panel__head">
            <span className="panel__num">05</span>
            <h3>recent activity</h3>
            <span className="panel__meta">last 3 days · live</span>
            <div className="panel__actions">
              <Btn>clear</Btn>
            </div>
          </div>
          <div className="actheader">
            <span>time</span>
            <span>recipient</span>
            <span>country</span>
            <span>sender</span>
          </div>
          <div className="activity">
            {activity.map((r, i) => (
              <div key={i} className="act">
                <span className="act__time">{r.time}</span>
                <span className="act__name">{r.name}</span>
                <span className="act__email">&lt;{r.email}&gt;</span>
                <span className="act__country">{r.country}</span>
                <span className="act__sender">{r.sender}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Weight({ email, value, onChange }) {
  return (
    <div className="weight">
      <span className="weight__email">{email}</span>
      <div className="weight__ctrl">
        <input
          type="range"
          min={0}
          max={100}
          value={value}
          onChange={(e) => onChange(+e.target.value)}
        />
        <input
          type="number"
          className="weight__num"
          value={value}
          onChange={(e) => onChange(+e.target.value)}
        />
        <span className="weight__unit">%</span>
      </div>
    </div>
  );
}

function Monitor({ monitorOn, setMonitorOn }) {
  const [interval, setInterval_] = useState(5);
  const [accounts, setAccounts] = useState([
    { email: "brianteller@blockchain-ltd-communications.com", on: true, replies: 14, checked: 431 },
    { email: "alexmartin@blockchain-ltd-communications.com", on: true, replies: 8, checked: 427 },
  ]);
  const [recent, setRecent] = useState([
    { time: "14:52:18", from: "gunnar1938@gmail.com", subj: "Re: quick question about your deposits", to: "brianteller", kind: "reply" },
    { time: "14:48:02", from: "mailer-daemon@gmx.de", subj: "Undelivered Mail Returned to Sender", to: "alexmartin", kind: "bounce" },
    { time: "14:41:33", from: "werner.koch@t-online.de", subj: "unsubscribe please", to: "alexmartin", kind: "unsub" },
    { time: "14:33:12", from: "peter.n@yahoo.com", subj: "Re: opportunity for depositors", to: "brianteller", kind: "reply" },
  ]);

  return (
    <div className="page page--monitor">
      <PageHeader
        title="reply monitor"
        cmd="pulse imap watch"
        desc="polls inboxes, pipes classified events into discord."
      />

      <div className="mon-grid">
        <section className="panel">
          <div className="panel__head">
            <span className="panel__num">01</span>
            <h3>monitor configuration</h3>
          </div>
          <div className="detail">
            <div className="field">
              <label>check inbox every (seconds)</label>
              <input type="number" value={interval} onChange={(e) => setInterval_(+e.target.value)} />
            </div>
            <div className="mon-accts">
              <div className="mon-accts__label">inbox accounts</div>
              {accounts.map((a, i) => (
                <label key={a.email} className="mon-acct">
                  <input
                    type="checkbox"
                    checked={a.on}
                    onChange={(e) => {
                      const next = [...accounts];
                      next[i] = { ...a, on: e.target.checked };
                      setAccounts(next);
                    }}
                  />
                  <span className="mon-acct__email">{a.email}</span>
                  <span className="mon-acct__meta">{a.replies} replies / {a.checked} checked</span>
                </label>
              ))}
            </div>
            <div className="note">
              <span className="note__tag">tip</span>
              make sure your discord webhook is configured in settings.
            </div>
            <div className="actions-row">
              <Btn tone="primary">save config</Btn>
              <Btn
                tone={monitorOn ? "danger" : "acid"}
                onClick={() => setMonitorOn(!monitorOn)}
              >
                {monitorOn ? "◼ stop" : "▶ start monitor"}
              </Btn>
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel__head">
            <span className="panel__num">02</span>
            <h3>reply feed</h3>
            <span className="panel__meta">live</span>
          </div>
          <div className="replies">
            {recent.map((r, i) => (
              <div key={i} className={`reply reply--${r.kind}`}>
                <div className="reply__top">
                  <span className="reply__time">{r.time}</span>
                  <Badge tone={r.kind === "reply" ? "acid" : r.kind === "bounce" ? "red" : "amber"}>
                    {r.kind}
                  </Badge>
                  <span className="reply__to">→ {r.to}</span>
                </div>
                <div className="reply__from">{r.from}</div>
                <div className="reply__subj">{r.subj}</div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel panel--wide">
          <div className="panel__head">
            <span className="panel__num">03</span>
            <h3>classifier rules</h3>
            <span className="panel__meta">regex · in order</span>
            <div className="panel__actions">
              <Btn>+ add rule</Btn>
            </div>
          </div>
          <div className="rules">
            {[
              { p: 1, name: "bounce", rx: "mailer-daemon|delivery status notification|undelivered", tone: "red" },
              { p: 2, name: "unsubscribe", rx: "unsubscribe|remove me|opt.?out", tone: "amber" },
              { p: 3, name: "positive", rx: "interested|tell me more|sounds good", tone: "acid" },
              { p: 4, name: "negative", rx: "not interested|stop|no thanks", tone: "mute" },
            ].map((r) => (
              <div key={r.p} className="rule">
                <span className="rule__p">{String(r.p).padStart(2, "0")}</span>
                <Badge tone={r.tone}>{r.name}</Badge>
                <code className="rule__rx">/{r.rx}/i</code>
                <button className="rule__del">×</button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Log() {
  const [filter, setFilter] = useState("all");
  const [lines, setLines] = useState([
    { t: "14:52:18", l: "info", src: "monitor", m: "reply matched 'positive' from gunnar1938@gmail.com" },
    { t: "14:52:01", l: "info", src: "send", m: "→ guillaumeauger30@gmail.com (CA) via alexmartin" },
    { t: "14:51:47", l: "info", src: "send", m: "→ quthoff@gmail.com (DE) via brianteller" },
    { t: "14:51:22", l: "warn", src: "smtp", m: "brianteller: rate limiter engaged, sleeping 8s" },
    { t: "14:51:03", l: "info", src: "send", m: "→ gschaffara111@gmail.com (AT) via alexmartin" },
    { t: "14:50:55", l: "err",  src: "smtp", m: "554 5.7.1 rejected by remote: gmx.de policy. skipping." },
    { t: "14:50:41", l: "info", src: "send", m: "→ guntliklimek63@gmail.com (DE) via brianteller" },
    { t: "14:50:12", l: "info", src: "monitor", m: "polled 2 inboxes · 0 new" },
    { t: "14:49:58", l: "info", src: "send", m: "→ gudrun.knorre@gmx.at (AT) via alexmartin" },
    { t: "14:49:33", l: "info", src: "send", m: "→ quinadio411@yahoo.com (SE) via brianteller" },
    { t: "14:49:22", l: "info", src: "db", m: "loaded import #58 · 1000 rows · 1000 with email" },
    { t: "14:49:21", l: "info", src: "boot", m: "pulse v2.1.0 online · 2 accounts ready" },
  ]);
  const filtered = filter === "all" ? lines : lines.filter((l) => l.l === filter);

  return (
    <div className="page page--log">
      <PageHeader
        title="log"
        cmd="pulse tail -f"
        desc="everything the engine does, in order, no spin."
        right={
          <div className="row-btns">
            <Btn>download .log</Btn>
            <Btn tone="danger">clear log</Btn>
          </div>
        }
      />

      <div className="log-filter">
        {["all", "info", "warn", "err"].map((f) => (
          <button
            key={f}
            className={`log-filter__btn ${filter === f ? "is-on" : ""}`}
            onClick={() => setFilter(f)}
          >
            [{f}]
          </button>
        ))}
        <div className="log-filter__spacer" />
        <label className="check"><input type="checkbox" defaultChecked /> auto-scroll</label>
        <label className="check"><input type="checkbox" /> wrap lines</label>
      </div>

      <div className="logview">
        {filtered.map((ln, i) => (
          <div key={i} className={`logln logln--${ln.l}`}>
            <span className="logln__t">{ln.t}</span>
            <span className={`logln__l logln__l--${ln.l}`}>{ln.l.toUpperCase().padEnd(4)}</span>
            <span className="logln__src">[{ln.src}]</span>
            <span className="logln__m">{ln.m}</span>
          </div>
        ))}
        <div className="logln logln--cursor">
          <span className="logln__t">{timeStr(new Date())}</span>
          <span className="logln__l logln__l--info">INFO</span>
          <span className="logln__src">[tty]</span>
          <span className="logln__m">waiting for events<span className="blink">_</span></span>
        </div>
      </div>
    </div>
  );
}

// ============ APP ============
function App() {
  const [page, setPage] = useState("outreach");
  const [outreachOn, setOutreachOn] = useState(true);
  const [monitorOn, setMonitorOn] = useState(true);

  useEffect(() => {
    const h = (e) => {
      if (e.key === "1") setPage("settings");
      if (e.key === "2") setPage("database");
      if (e.key === "3") setPage("outreach");
      if (e.key === "4") setPage("monitor");
      if (e.key === "5") setPage("log");
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  return (
    <div className="shell">
      <TopBar page={page} setPage={setPage} outreachOn={outreachOn} monitorOn={monitorOn} />
      <main className="main" data-screen-label={page}>
        {page === "settings" && <Settings />}
        {page === "database" && <Database />}
        {page === "outreach" && <Outreach outreachOn={outreachOn} setOutreachOn={setOutreachOn} />}
        {page === "monitor" && <Monitor monitorOn={monitorOn} setMonitorOn={setMonitorOn} />}
        {page === "log" && <Log />}
      </main>
      <StatusBar page={page} outreachOn={outreachOn} monitorOn={monitorOn} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
