CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, batch_id TEXT NOT NULL, title TEXT NOT NULL,
 author TEXT NOT NULL DEFAULT 'Unknown', institution TEXT NOT NULL DEFAULT '', document_type TEXT NOT NULL DEFAULT 'document',
 published_at TEXT, filename TEXT NOT NULL, mime_type TEXT NOT NULL, sha256 TEXT NOT NULL,
 status TEXT NOT NULL, word_count INTEGER NOT NULL DEFAULT 0, page_count INTEGER NOT NULL DEFAULT 0,
 source_url TEXT NOT NULL DEFAULT '', license TEXT NOT NULL DEFAULT '', object_key TEXT,
 blocks_key TEXT, created_at TEXT NOT NULL, error TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS documents_project ON documents(project_id, created_at);
CREATE INDEX IF NOT EXISTS documents_digest ON documents(project_id, sha256);
CREATE TABLE IF NOT EXISTS batches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_members (
 batch_id TEXT NOT NULL, document_id TEXT NOT NULL, PRIMARY KEY(batch_id,document_id)
);
CREATE INDEX IF NOT EXISTS batch_members_document ON batch_members(document_id);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL, config_json TEXT NOT NULL,
 created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, progress_json TEXT NOT NULL DEFAULT '{}',
 usage_json TEXT NOT NULL DEFAULT '{}', errors_json TEXT NOT NULL DEFAULT '[]', model_profile_json TEXT NOT NULL DEFAULT '{}',
 reserved_usd REAL NOT NULL DEFAULT 0, spent_usd REAL NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_project ON runs(project_id, created_at);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, document_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
 start_block INTEGER NOT NULL, end_block INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
 result_key TEXT, coverage_json TEXT NOT NULL DEFAULT '{}', usage_json TEXT NOT NULL DEFAULT '{}', error TEXT,
 UNIQUE(run_id, document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS tasks_run ON tasks(run_id, status, ordinal);
CREATE TABLE IF NOT EXISTS findings (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL, document_id TEXT NOT NULL,
 task_id TEXT NOT NULL, lens_id TEXT NOT NULL, category TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'unreviewed',
 review_note TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS findings_project ON findings(project_id, run_id, document_id);
CREATE INDEX IF NOT EXISTS findings_filter ON findings(project_id, lens_id, category, review_status);
CREATE TABLE IF NOT EXISTS reviews (
 id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, review_status TEXT NOT NULL, review_note TEXT NOT NULL,
 previous_status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, format TEXT NOT NULL, profile TEXT NOT NULL,
 object_key TEXT NOT NULL, snapshot_key TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS budget_ledger (
 month TEXT PRIMARY KEY, reserved_usd REAL NOT NULL DEFAULT 0, spent_usd REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS budget_reservations (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT NOT NULL, month TEXT NOT NULL,
 amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'reserved', charged_usd REAL,
 usage_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, settled_at TEXT
);
CREATE INDEX IF NOT EXISTS budget_reservations_task ON budget_reservations(task_id,status);
