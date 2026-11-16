CREATE TABLE IF NOT EXISTS cloud_uploads (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, object_key TEXT NOT NULL,
 r2_upload_id TEXT NOT NULL, filename TEXT NOT NULL, mime_type TEXT NOT NULL,
 size INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'uploading',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, result_json TEXT
);
CREATE TABLE IF NOT EXISTS cloud_upload_parts (
 upload_id TEXT NOT NULL, part_number INTEGER NOT NULL, etag TEXT NOT NULL, size INTEGER NOT NULL,
 PRIMARY KEY (upload_id, part_number)
);
CREATE TABLE IF NOT EXISTS cloud_dispatch (
 run_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
 updated_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT
);
CREATE INDEX IF NOT EXISTS cloud_dispatch_pending ON cloud_dispatch(status, updated_at);
