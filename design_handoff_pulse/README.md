# Handoff: Pulse Console Redesign (Flask)

## Overview
A full redesign of the Pulse outreach tool — a 5-page console (Settings, Database, Outreach, Monitor, Log) for managing cold-email campaigns across multiple sender accounts. The new look is fly.io-inspired: mono typography, near-black background, acid green accent, terminal/ASCII ornaments, numbered panels.

## About the Design Files
The files in this bundle (`Pulse.html`, `app.jsx`, `styles.css`) are **design references created in HTML/React**. They are *prototypes* showing the intended look and behavior — not production code to copy verbatim.

The target codebase is a **Flask** app. The task is to recreate these designs using Flask's normal patterns:

- **Jinja2 templates** for structure — one base layout with the top bar + status bar, and a template per page (`settings.html`, `database.html`, `outreach.html`, `monitor.html`, `log.html`) extending it.
- **`styles.css` can be used almost as-is** — drop it into `static/css/` and link it from the base template. The only thing to change is nothing CSS-wise; it has no React assumptions.
- **Server-rendered HTML with progressive enhancement**. The React JSX is there to demonstrate *interactions* — for Flask, the rule of thumb is:
  - Static structure, forms, navigation → Jinja.
  - Live/streaming bits (outreach progress, reply feed, log tail) → small vanilla JS islands that poll a JSON endpoint or read from an SSE/WebSocket stream. **htmx** is a great fit and will keep the "hacky terminal" feel.
  - No React, no SPA, no build step required.

## Fidelity
**High-fidelity.** Colors, spacing, typography, and interactions in the prototype are final and should be matched pixel-for-pixel where practical.

## Suggested Flask Project Structure

```
pulse/
├── app.py                  # Flask app, routes
├── templates/
│   ├── base.html           # topbar + statusbar + {% block content %}
│   ├── _components.html    # Jinja macros: panel(), stat(), badge(), btn(), field()
│   ├── settings.html
│   ├── database.html
│   ├── outreach.html
│   ├── monitor.html
│   └── log.html
├── static/
│   ├── css/styles.css      # COPIED AS-IS from this handoff
│   ├── js/
│   │   ├── topbar.js       # clock, status dots polling
│   │   ├── outreach.js     # progress polling, activity feed append
│   │   └── log.js          # log tail polling
│   └── img/
│       ├── favicon.png
│       └── pulse_logo.png
```

### Routes
```
GET  /                      → redirect to /outreach
GET  /settings              → settings.html
POST /settings/account      → save/add account
POST /settings/webhook      → save webhook
GET  /database              → database.html
POST /database/import       → file upload (.db or .xlsx)
GET  /api/import/<id>       → JSON (stats + location breakdown)
GET  /outreach              → outreach.html
POST /outreach/start        → kick off background job
POST /outreach/stop         → stop
GET  /api/outreach/progress → JSON {sent, target, eta}
GET  /api/outreach/activity → JSON [last N sends] (poll every 1–2s)
GET  /monitor               → monitor.html
POST /monitor/start
POST /monitor/stop
GET  /api/monitor/replies   → JSON [last N classified events]
GET  /log                   → log.html (renders last 500 lines)
GET  /api/log?since=<ts>    → JSON for tail
POST /log/clear
```

## Pages (What To Build)

### Chrome (every page)
- **Topbar** (`.topbar`): logo + "pulse" wordmark + `v2.1.0` pill → 5 tabs `[settings] [database] [outreach] [monitor] [log]` → right side has two `sys-stat` pills (outreach / monitor running|idle), user email, sign-out.
  - Keyboard shortcuts `1`-`5` jump tabs. Bind in `topbar.js`.
  - The status pills should reflect backend state — poll `/api/outreach/progress` and `/api/monitor/replies?head=1` every 5s, or push via SSE.
- **Statusbar** (`.statusbar`): left shows `~/pulse / <page>`, middle shows account/webhook/SMTP/IMAP health, right shows `? help` `⌘K cmd` and a live clock. Render initial state server-side; `topbar.js` updates clock each second.

### 1. Settings (`/settings`)
Three panels:
- **01 email accounts** — list of configured SMTP accounts with dot (on/off), email, `sent/daily today`, warmup %.
- **02 account detail** — form for the currently-selected account. Fields: SMTP host/port, IMAP host/port, from name, daily limit, warmup bar. Buttons: save / test connection / remove. Selection is client-side (JS swap) or server-side (`?selected=<id>`).
- **03 discord webhook** (full-width) — URL input (monospace, scrollable), test ping + save, then 4 event checkboxes (on reply, on bounce, on send verbose, on daily summary).

### 2. Database (`/database`)
Two-column grid:
- **Left — 01 imports**: search/filter box + vertical list. Each item: `#<id>`, name, lead count, date, mini progress bar (sent/leads), pct. Top-right page actions: `+ import .db`, `▦ import excel`, `↻ refresh`.
- **Right — 02 detail**: `<name>.xlsx` heading, 4 stat tiles (leads with email / sent / replied / left not sent). ASCII rule labelled "location breakdown". List of countries with horizontal bar + count + pct.

### 3. Outreach (`/outreach`)
Five panels in a 3-column grid (last two span full width):
- **01 list selection** — dropdown of imports + note.
- **02 sender weights** — one slider + number + `%` per account. Auto-normalizes.
- **03 sending parameters** — daily limit, min/max delay (sec), send-simultaneously checkbox, save + start/stop buttons.
- **04 progress** (full width) — huge `sent / target pct%` row + 14px progress bar with 40 tick marks + meta (eta, avg/send, running/idle). Poll `/api/outreach/progress` every 1s while running.
- **05 recent activity** (full width) — streaming table. Columns: time, recipient name, `<email>`, country (acid green), sender (violet). Poll `/api/outreach/activity?since=<lastId>` and prepend new rows.

### 4. Monitor (`/monitor`)
Two-column + full-width:
- **01 monitor configuration** — check-interval field, checkboxes per inbox account, tip note, save + start/stop.
- **02 reply feed** — list of events. Each has time, badge (`reply`/`bounce`/`unsub`), → sender, from-address, subject. Left-border color encodes kind.
- **03 classifier rules** (full width) — ordered regex rules. Each row: priority `01/02/…`, name badge, `/regex/i` in acid-green monospace chip, × delete button. `+ add rule` in header.

### 5. Log (`/log`)
- Filter bar: `[all] [info] [warn] [err]` toggle + `auto-scroll` + `wrap lines` checkboxes.
- Terminal viewport: each line is `HH:MM:SS  LEVEL  [src]  message`. Levels colored acid/amber/red. Source in violet.
- Ends with a blinking `_` cursor line that shows current time and `waiting for events`.
- Poll `/api/log?since=<lastTs>` every 2s, append in DOM. If `auto-scroll` is checked, keep scrolled to bottom.

## Design Tokens (all in `:root` in styles.css — keep these exact)

### Colors
```
--bg        #0a0d0a   near-black
--bg-2      #0f1410   panel background
--bg-3      #141a15   input/button background
--bg-4      #1a211b   hover background
--line      #1e2820   hairline borders
--line-2    #2a3630   secondary borders
--ink       #e6efe8   primary text
--ink-2     #c2cfc5   secondary text
--mute      #6a7a6d   meta/labels
--mute-2    #485149   dim meta
--acid      #2dd474   pulse green (primary accent)
--acid-2    #1ea55a   darker green (gradients)
--acid-dim  #0f3a22   green-tinted background
--amber     #f4b842   warnings
--red       #ff5c5c   errors/danger
--violet    #a88bff   secondary accent (sender, source)
```

### Typography
- Mono: **JetBrains Mono** (300–700) — used for almost everything
- Sans: **IBM Plex Sans** (400–700) — available but rarely used
- Sizes: 10px (labels, caps), 11px (meta), 12px (body), 13px (base), 14–28px (stats), 32px (page title), 42px (big progress number)
- Page titles are mono 32px weight 500, prefixed with a green `#`. Labels use `text-transform: uppercase; letter-spacing: 0.08em`.

### Spacing & Borders
- Radius: 2–4px everywhere (never more). Primitive, blocky look.
- Panel padding: 12–16px header, 16–20px body.
- Grid gap between panels: 16px.
- Dashed dividers (`border-bottom: 1px dashed var(--line-2)`) for panel heads; solid for section breaks.

### Signature Details (don't miss these)
- **Scanlines**: `body::before` draws a 1px-every-3px horizontal line repeat at ~0.008 alpha. Global.
- **Faint grid**: `.main::before` draws an 80×80px grid at 0.25 opacity, masked to fade at edges.
- **ASCII rules**: `.ascii-rule` components — `── LABEL ──` followed by a long `─` line. Used as section dividers inside panels.
- **Numbered panels**: every panel head has a small `01`/`02`/… chip in acid-green on acid-dim background.
- **Panel top hairline**: `.panel::before` overlays a faint green gradient on the top edge.
- **Progress bar**: gradient fill + 40 tick marks overlaid + green glow (`box-shadow: 0 0 12px rgba(45,212,116,0.4)`).
- **Blinking cursor**: `_` char animated with `@keyframes blink` at 1s steps(2).

### Components (Jinja macro suggestions)
```jinja
{% macro panel(num, title, meta=None) %}
  <section class="panel">
    <div class="panel__head">
      <span class="panel__num">{{ "%02d"|format(num) }}</span>
      <h3>{{ title }}</h3>
      {% if meta %}<span class="panel__meta">{{ meta }}</span>{% endif %}
      {{ caller() if caller else "" }}
    </div>
    {{ caller() }}
  </section>
{% endmacro %}

{% macro btn(label, tone="default", icon=None, type="button") %}
  <button class="btn btn--{{ tone }}" type="{{ type }}">
    {% if icon %}<span class="btn__icon">{{ icon }}</span>{% endif %}
    {{ label }}
  </button>
{% endmacro %}

{% macro badge(label, tone="default") %}
  <span class="badge badge--{{ tone }}">{{ label }}</span>
{% endmacro %}

{% macro stat(label, value, tone="default") %}
  <div class="stat stat--{{ tone }}">
    <div class="stat__val">{{ value }}</div>
    <div class="stat__label">{{ label }}</div>
    <div class="stat__corner">┐</div>
  </div>
{% endmacro %}

{% macro dot(state="off") %}
  <span class="dot dot--{{ state }}"></span>
{% endmacro %}
```
(Add `.dot--on { background: var(--acid); box-shadow: 0 0 8px var(--acid); }` etc. to styles.css since the prototype inlined this.)

## Live-Update Patterns

All the "live" bits in the prototype (progress, activity, reply feed, log) are driven by React `setInterval`s. For Flask, pick one of:

1. **htmx polling** (simplest): Add `hx-get="/api/outreach/progress" hx-trigger="every 1s" hx-swap="outerHTML"` to the progress panel. Return a partial template fragment. Same pattern for activity (use `hx-swap="afterbegin"` to prepend).
2. **Vanilla `setInterval` + `fetch`**: parse JSON, swap `innerHTML`. Matches the React code 1:1.
3. **Server-Sent Events** (`flask-sse`): best for the log tail. `new EventSource('/api/log/stream')` and append lines as they arrive.

The outreach and monitor workers themselves should run in a background thread/process (APScheduler or a simple `threading.Thread`) — the HTTP request that hits `/outreach/start` just flips a flag the worker reads.

## Forms

All forms are plain `<form method="post">` → CSRF-protected Flask route → redirect back with `flash()`. Fields already have the correct `.field` styling.

## Assets
- `assets/favicon.png` — 1024×1024 green logo mark, use as `/static/img/favicon.png`
- `assets/pulse_logo.png` — wordmark, reference only (the UI uses the mark + HTML text "pulse", not the wordmark image)

## Files in this bundle
- `Pulse.html` — entry point of the prototype (load in a browser to see it)
- `app.jsx` — all the React components + seed data. Treat as a behavioral spec.
- `styles.css` — **use as-is** in Flask. No modifications needed.
- `assets/` — logo + favicon

## Mobile / Responsive

The prototype is fully responsive. Media queries in `styles.css` already cover:

- **≤1100px (tablet)**: Settings/Database/Monitor grids collapse to one column; Outreach goes to two columns.
- **≤720px (phone)**:
  - Topbar collapses into a two-row stack. A hamburger toggle (`≡ <current-page>`) shows instead of the tab strip; tapping it reveals a stacked vertical menu.
  - All panels go single-column.
  - Stat tiles go 2×2 (instead of 1×4).
  - Activity rows (Outreach) and Log lines reflow into card-style multi-line layouts using `grid-template-areas`.
  - Status bar drops its middle section; tabs/buttons hit 38–44px minimum touch height.
  - `body::before` scanlines are disabled; background grid density halves.
- **≤400px (narrow phone)**: Further font-size and padding reductions.

### Flask implementation note
In the Jinja version, the hamburger toggle needs to show/hide the `.tabs` element. Two options:
1. **Pure CSS**: use a hidden checkbox + `:checked ~ .tabs { display: flex; }` sibling trick. No JS.
2. **Tiny JS**: in `topbar.js`, toggle an `is-open` class on `.tabs` when the button is clicked. Add `.tabs.is-open { display: flex; }` is already in the CSS — you just need to wire the click.

```html
<button class="menu-toggle" id="menuToggle" aria-label="toggle menu">
  ≡ <span style="font-size:11px">{{ current_page }}</span>
</button>
<nav class="tabs" id="tabs">...</nav>

<script>
  document.getElementById('menuToggle').addEventListener('click', () => {
    document.getElementById('tabs').classList.toggle('is-open');
  });
</script>
```

Also add `<meta name="viewport" content="width=device-width, initial-scale=1">` to `base.html` — critical for the media queries to fire correctly on real phones.

## Notes
- Don't redesign. Every color, border, spacing choice in `styles.css` is intentional.
- Don't add rounded-corner cards or colorful gradients. The aesthetic is **primitive / blocky / terminal**.
- Don't add icon libraries (Feather, Lucide, etc). The prototype uses unicode glyphs `▶ ◼ ↻ ▦ + ─ ┐` on purpose. Keep it that way.
- Keyboard `1`-`5` tab switching should be preserved (desktop).
- Don't strip the mobile CSS — the tool is meant to be usable on a phone for quick checks.
