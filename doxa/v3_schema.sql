CREATE TABLE IF NOT EXISTS schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS v3_sources (
 document_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_identity TEXT NOT NULL,
 content_hash TEXT NOT NULL, canonical_url TEXT NOT NULL DEFAULT '', relative_path TEXT NOT NULL DEFAULT '',
 version TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v3_run_sources (
 run_id TEXT NOT NULL, document_id TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,document_id)
);
CREATE INDEX IF NOT EXISTS v3_sources_identity ON v3_sources(project_id,source_identity,content_hash);
CREATE TABLE IF NOT EXISTS v3_assessments (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL, document_id TEXT NOT NULL,
 task_id TEXT NOT NULL DEFAULT '', unit_id TEXT NOT NULL, lens_id TEXT NOT NULL, criterion_id TEXT NOT NULL,
 criterion_kind TEXT NOT NULL, result TEXT NOT NULL, attribution TEXT NOT NULL DEFAULT 'unknown',
 speech_role TEXT NOT NULL DEFAULT 'unknown', speaker_stance TEXT NOT NULL DEFAULT 'unclear',
 document_stance TEXT NOT NULL DEFAULT 'unclear', hypothetical INTEGER NOT NULL DEFAULT 0,
 review_status TEXT NOT NULL DEFAULT 'unreviewed', review_note TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL, review_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v3_assessments_scope ON v3_assessments(project_id,run_id,result,criterion_kind,id);
CREATE INDEX IF NOT EXISTS v3_assessments_document ON v3_assessments(run_id,document_id,unit_id);
CREATE INDEX IF NOT EXISTS v3_assessments_criterion ON v3_assessments(run_id,lens_id,criterion_id,result);
CREATE INDEX IF NOT EXISTS v3_assessments_review ON v3_assessments(project_id,run_id,review_status,speech_role);
CREATE TABLE IF NOT EXISTS v3_unit_statuses (
 run_id TEXT NOT NULL, document_id TEXT NOT NULL, unit_id TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL, characters INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(run_id,document_id,unit_id)
);
CREATE INDEX IF NOT EXISTS v3_units_run ON v3_unit_statuses(run_id,status,document_id);
CREATE TABLE IF NOT EXISTS v3_query_snapshots (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scope_hash TEXT NOT NULL,
 snapshot_version TEXT NOT NULL, query_json TEXT NOT NULL, aggregates_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS v3_queries_project ON v3_query_snapshots(project_id,created_at);
CREATE TABLE IF NOT EXISTS v3_imports (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, batch_id TEXT NOT NULL, name TEXT NOT NULL,
 total_files INTEGER NOT NULL DEFAULT 0, completed_files INTEGER NOT NULL DEFAULT 0,
 failed_files INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v3_import_files (
 import_id TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL,
 document_id TEXT, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
 PRIMARY KEY(import_id,relative_path,sha256)
);
INSERT INTO schema_versions(version,applied_at) VALUES(3,CURRENT_TIMESTAMP) ON CONFLICT(version) DO NOTHING;
