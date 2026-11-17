"""Fresh cloud initialization uses the real migrator without any live resource."""
from pathlib import Path

from doxa.store import Store


def test_infrastructure_only_cloud_database_bootstraps_without_upgrade_receipt(tmp_path, monkeypatch):
    monkeypatch.delenv("DOXA_MIGRATION_BACKUP_RECEIPT", raising=False)
    monkeypatch.delenv("DOXA_CLOUD_STATE_URL", raising=False)
    local = Store(tmp_path)
    try:
        infrastructure = Path(__file__).resolve().parents[1] / "infra/cloudflare/migrations/0001_cloud.sql"
        local.batch([{"sql": sql, "params": []} for sql in infrastructure.read_text().split(";") if sql.strip()])
        assert local.one("SELECT name FROM sqlite_master WHERE name='documents'") is None
        # The bridge delegates SQL to an isolated native database. The actual
        # remote initialization branch is used; no HTTP or provider is invoked.
        class CloudBridge:
            remote = "https://isolated-test.invalid"
            _db = None
            query = local.query
            one = local.one
            execute = local.execute
            batch = local.batch
        cloud = CloudBridge()
        Store.init(cloud)
        assert local.one("SELECT MAX(version) version FROM schema_versions")["version"] == 4
        assert local.one("SELECT COUNT(*) n FROM documents")["n"] == 0
        for name in ("cloud_dispatch", "cloud_uploads", "report_jobs", "report_job_parts", "v4_casebooks"):
            assert local.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name])
    finally:
        local.close()
