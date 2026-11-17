CREATE TABLE IF NOT EXISTS report_jobs (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, query_id TEXT NOT NULL,
 casebook_id TEXT, casebook_revision INTEGER, publication_type TEXT NOT NULL,
 format TEXT NOT NULL, profile TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 phase TEXT NOT NULL DEFAULT 'queued', scope_hash TEXT NOT NULL, snapshot_version TEXT NOT NULL,
 config_key TEXT NOT NULL, state_json TEXT NOT NULL DEFAULT '{}', progress_json TEXT NOT NULL DEFAULT '{}',
 error_json TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
 lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS report_jobs_project ON report_jobs(project_id,created_at,id);
CREATE INDEX IF NOT EXISTS report_jobs_pending ON report_jobs(status,lease_until,created_at);
/* Result/criterion indexes cannot satisfy the global ID cursor.
   Avoid a full-run payload scan and temporary sort for every page. */
CREATE INDEX IF NOT EXISTS v3_assessments_report_cursor ON v3_assessments(run_id,id);
CREATE TABLE IF NOT EXISTS report_job_artifacts (
 job_id TEXT NOT NULL, format TEXT NOT NULL, object_key TEXT NOT NULL, filename TEXT NOT NULL,
 bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, content_type TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(job_id,format)
);
CREATE TABLE IF NOT EXISTS report_job_parts (
 job_id TEXT NOT NULL, stage TEXT NOT NULL, ordinal INTEGER NOT NULL,
 object_key TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
 row_count INTEGER NOT NULL, last_key TEXT NOT NULL,
 PRIMARY KEY(job_id,stage,ordinal)
);
