-- Run this entire file in Supabase → SQL Editor

CREATE TABLE IF NOT EXISTS email_accounts (
    id TEXT PRIMARY KEY,
    label TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    smtp_host TEXT DEFAULT 'smtp.alexhost.com',
    smtp_port TEXT DEFAULT '465',
    smtp_user TEXT DEFAULT '',
    smtp_password TEXT DEFAULT '',
    imap_host TEXT DEFAULT 'imap.alexhost.com',
    imap_port TEXT DEFAULT '993',
    outreach_subject TEXT DEFAULT '',
    outreach_body TEXT DEFAULT '',
    weight INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS imports (
    id BIGSERIAL PRIMARY KEY,
    label TEXT DEFAULT '',
    imported_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS leads (
    id BIGSERIAL PRIMARY KEY,
    import_id BIGINT NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    full_name TEXT DEFAULT '',
    number TEXT DEFAULT '',
    sent BOOLEAN NOT NULL DEFAULT FALSE,
    replied BOOLEAN NOT NULL DEFAULT FALSE,
    sent_at TIMESTAMPTZ,
    replied_at TIMESTAMPTZ,
    UNIQUE(import_id, email)
);

CREATE INDEX IF NOT EXISTS idx_leads_import_unsent ON leads(import_id) WHERE sent = FALSE;
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);

CREATE TABLE IF NOT EXISTS outreach_history (
    id BIGSERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    name TEXT DEFAULT '',
    email TEXT DEFAULT '',
    number TEXT DEFAULT '',
    location TEXT DEFAULT '',
    sender_label TEXT DEFAULT '',
    sender_email TEXT DEFAULT '',
    import_id BIGINT,
    ts TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outreach_history_date ON outreach_history(date);

CREATE TABLE IF NOT EXISTS processed_replies (
    account_key TEXT NOT NULL,
    message_id TEXT NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (account_key, message_id)
);

CREATE TABLE IF NOT EXISTS phone_country_cache (
    number_sanitized TEXT PRIMARY KEY,
    country_code TEXT DEFAULT '',
    country_label TEXT DEFAULT '',
    checked_at TIMESTAMPTZ DEFAULT NOW()
);

-- Disable RLS so the server key can read/write freely
ALTER TABLE email_accounts    DISABLE ROW LEVEL SECURITY;
ALTER TABLE app_config        DISABLE ROW LEVEL SECURITY;
ALTER TABLE imports           DISABLE ROW LEVEL SECURITY;
ALTER TABLE leads             DISABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_history  DISABLE ROW LEVEL SECURITY;
ALTER TABLE processed_replies DISABLE ROW LEVEL SECURITY;
ALTER TABLE phone_country_cache DISABLE ROW LEVEL SECURITY;
