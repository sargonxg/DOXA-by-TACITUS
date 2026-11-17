CREATE TABLE IF NOT EXISTS v4_casebooks (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, query_id TEXT NOT NULL, run_id TEXT NOT NULL,
 scope_hash TEXT NOT NULL, snapshot_version TEXT NOT NULL, scope_key TEXT NOT NULL,
 scope_sha256 TEXT NOT NULL, title TEXT NOT NULL, selection_rationale TEXT NOT NULL DEFAULT '',
 publication_type TEXT NOT NULL DEFAULT 'casebook', revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v4_casebooks_project ON v4_casebooks(project_id,created_at,id);
CREATE TABLE IF NOT EXISTS v4_casebook_cases (
 id TEXT PRIMARY KEY, casebook_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
 kind TEXT NOT NULL, document_id TEXT NOT NULL, unit_id TEXT NOT NULL,
 assessment_id TEXT, snapshot_key TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
 selection_rationale TEXT NOT NULL DEFAULT '', editorial_note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(casebook_id,kind,document_id,unit_id,assessment_id)
);
CREATE INDEX IF NOT EXISTS v4_casebook_cases_book ON v4_casebook_cases(casebook_id,ordinal,id);
