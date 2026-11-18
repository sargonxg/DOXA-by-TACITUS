#!/bin/bash
# Explicit root recovery into EMPTY customer data; never overwrites a live installation.
set -euo pipefail
umask 077
if [ "$#" -ne 1 ]; then
  echo 'Usage: /opt/doxa/restore.sh backups/<timestamp>-<sha256>.tar.zst' >&2
  exit 2
fi
config=/opt/doxa/compose.yaml
exec 9>/run/doxa-backup.lock
flock -n 9 || { echo 'Another backup or restore is active.' >&2; exit 1; }
docker compose -f "$config" stop app
if [ -n "$(find /srv/doxa-data/data -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo 'Restore requires an empty data directory. Preserve or archive existing data deliberately before retrying.' >&2
  exit 1
fi
backup_bucket=$(python3 -c 'import json;print(json.load(open("/opt/doxa/operator.json"))["backup_bucket"])')
control_bucket=$(python3 -c 'import json;print(json.load(open("/opt/doxa/operator.json"))["control_bucket"])')
installation=$(python3 /opt/doxa/cloud_store.py installation-id /run/doxa-secrets/pilot.env)
# Current external ledger MUST be recovered before applying a customer-data backup.
python3 /opt/doxa/cloud_store.py latest-ledger "$control_bucket" "$installation" /run/doxa-restore-ledger.json
python3 /opt/doxa/cloud_store.py merge-ledger /run/doxa-restore-ledger.json /var/lib/doxa-control/deletions/ledger.json "$installation"
chown 10001:10001 /var/lib/doxa-control/deletions/ledger.json
archive=/srv/doxa-data/scratch/restore-$(date +%s)-$$.tar.zst
trap 'rm -f -- "$archive"' EXIT
python3 /opt/doxa/cloud_store.py backup-download "$backup_bucket" "$1" "$archive"
python3 - "$archive" /srv/doxa-data/data <<'PY'
import os,subprocess,sys,tarfile
from pathlib import Path
archive,target=Path(sys.argv[1]),Path(sys.argv[2])
process=subprocess.Popen(['zstd','--decompress','--stdout',str(archive)],stdout=subprocess.PIPE)
try:
    with tarfile.open(fileobj=process.stdout,mode='r|') as source:
        for member in source:
            relative=Path(member.name)
            if relative.is_absolute() or '..' in relative.parts or not(member.isdir() or member.isreg()):
                raise ValueError('Unsafe archive member; recovery stopped before serving data')
            if not (target/relative).resolve().is_relative_to(target.resolve()):
                raise ValueError('Archive member escapes the customer data directory')
            source.extract(member,target,filter='data')
    if process.wait()!=0:
        raise ValueError('Backup decompression failed')
finally:
    if process.poll() is None:
        process.terminate()
        process.wait()
for root,dirs,files in os.walk(target):
    os.chown(root,10001,10001)
    for name in files:
        os.chown(Path(root)/name,10001,10001,follow_symlinks=False)
PY
# Migration plus signed-ledger replay must succeed before reopening the app.
docker compose -f "$config" run --rm --no-deps app python /app/scripts/privacy_lifecycle_v06.py restore-check --data-dir /data
docker compose -f "$config" up -d app
rm -- "$archive"
echo 'Customer data restored and deletion obligations replayed. Verify source/report hashes and access before customer handover.'
