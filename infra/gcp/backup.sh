#!/bin/bash
# Root operator action; briefly stops writes to capture DB, WAL and objects together.
set -euo pipefail
umask 077
config=/opt/doxa/compose.yaml
bucket=$(python3 -c 'import json;print(json.load(open("/opt/doxa/operator.json"))["backup_bucket"])')
archive=$(mktemp /srv/doxa-data/scratch/backup-XXXXXXXX.tar.zst)
exec 9>/run/doxa-backup.lock
flock -n 9 || { echo 'Another backup or restore is active.' >&2; exit 1; }
restart_app() { docker compose -f "$config" up -d app >/dev/null; }
finish_failed_capture() {
  result=$?
  restart_app || true
  rm -f -- "$archive"
  exit "$result"
}
trap finish_failed_capture EXIT
docker compose -f "$config" stop app
# No app writer remains. Includes SQLite WAL and all objects, excludes external control/secrets.
tar --hard-dereference --numeric-owner -C /srv/doxa-data/data -I 'zstd -T1 -3' -cf "$archive" .
restart_app
trap 'rm -f -- "$archive"' EXIT
python3 /opt/doxa/cloud_store.py backup-upload "$bucket" "$archive"
rm -- "$archive"
trap - EXIT
