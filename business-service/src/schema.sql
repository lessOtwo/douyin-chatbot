PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL, external_id TEXT NOT NULL,
 customer_id TEXT NOT NULL, session_id TEXT, revision INTEGER NOT NULL DEFAULT 0,
 epoch INTEGER NOT NULL DEFAULT 0,
 mode TEXT NOT NULL DEFAULT 'bot', customer_nickname TEXT NOT NULL DEFAULT '',
 is_peer INTEGER NOT NULL DEFAULT 0, reply_rounds INTEGER NOT NULL DEFAULT 0,
 project_engaged INTEGER NOT NULL DEFAULT 0, contact_refused INTEGER NOT NULL DEFAULT 0,
 contact_promised INTEGER NOT NULL DEFAULT 0,
 UNIQUE(account_id,external_id)
);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL, event_id TEXT NOT NULL,
 message_id TEXT NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 text TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
 image_mime TEXT, image_data BLOB, image_sha256 TEXT,
 error TEXT, created_at INTEGER NOT NULL DEFAULT (unixepoch()),
 UNIQUE(account_id,event_id), UNIQUE(account_id,message_id)
);
CREATE TABLE IF NOT EXISTS decisions (
 event_id TEXT PRIMARY KEY REFERENCES events(id), result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id), revision INTEGER NOT NULL,
 text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', receipt TEXT, error TEXT,
 created_at INTEGER NOT NULL DEFAULT (unixepoch()), sent_at INTEGER,
 source TEXT NOT NULL DEFAULT 'bot', part_index INTEGER NOT NULL DEFAULT 0,
 UNIQUE(event_id,part_index)
);
CREATE TABLE IF NOT EXISTS leads (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 event_id TEXT NOT NULL REFERENCES events(id), kind TEXT NOT NULL, value TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'candidate', created_at INTEGER NOT NULL DEFAULT (unixepoch()),
 exported_at INTEGER,
 UNIQUE(conversation_id,kind,value)
);
CREATE TABLE IF NOT EXISTS lead_history_links (
 token_hash TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
 max_event_rowid INTEGER NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()),
 expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS lead_history_links_expiry ON lead_history_links(expires_at);
CREATE INDEX IF NOT EXISTS event_queue ON events(status,created_at);
CREATE INDEX IF NOT EXISTS outbox_queue ON outbox(status,created_at);

CREATE TABLE IF NOT EXISTS unanswered_questions (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE REFERENCES events(id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id),
 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
 created_at INTEGER NOT NULL DEFAULT (unixepoch()), updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS unanswered_queue ON unanswered_questions(status,created_at);

CREATE TABLE IF NOT EXISTS industries (
 id TEXT PRIMARY KEY,
 draft_name TEXT NOT NULL, draft_question TEXT NOT NULL,
 published_name TEXT, published_question TEXT,
 published_at INTEGER, updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE IF NOT EXISTS industry_projects (
 id TEXT PRIMARY KEY, industry_id TEXT NOT NULL REFERENCES industries(id),
 draft_name TEXT NOT NULL, draft_question TEXT NOT NULL,
 published_name TEXT, published_question TEXT,
 published_at INTEGER, updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS industry_projects_by_industry ON industry_projects(industry_id,published_at);
CREATE TABLE IF NOT EXISTS removed_accounts (
 id TEXT PRIMARY KEY, removed_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE IF NOT EXISTS merchants (
 id TEXT PRIMARY KEY, current_version_id TEXT,
 bot_paused INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE IF NOT EXISTS merchant_accounts (
 id TEXT PRIMARY KEY,
 merchant_id TEXT NOT NULL UNIQUE REFERENCES merchants(id),
 douyin_nickname TEXT NOT NULL,
 industry TEXT NOT NULL,
 fastgpt_app_id TEXT NOT NULL DEFAULT '',
 reply_window_seconds INTEGER NOT NULL DEFAULT 86400,
 created_at INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE IF NOT EXISTS merchant_versions (
 id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL REFERENCES merchants(id),
 version INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
 profile_json TEXT NOT NULL, contact_name TEXT NOT NULL, contact_phone TEXT NOT NULL,
 submitted_at INTEGER NOT NULL DEFAULT (unixepoch()), reviewed_at INTEGER,
 review_reason TEXT NOT NULL DEFAULT '',
 UNIQUE(merchant_id,version)
);
CREATE INDEX IF NOT EXISTS merchant_version_queue ON merchant_versions(status,submitted_at);
CREATE TABLE IF NOT EXISTS merchant_invitations (
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
 merchant_id TEXT REFERENCES merchants(id), status TEXT NOT NULL CHECK(status IN ('active','used','revoked')),
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()), used_at INTEGER
);


