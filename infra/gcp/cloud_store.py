#!/usr/bin/env python3
"""Least-privilege GCE storage helper; no cloud SDK or service-account key file.

Deletion ledgers use immutable, generation-zero GCS objects per sequence.
Publication verifies monotonic complete history and is safe to retry. This
program receives canonical signed ledger bytes on stdin; it never logs them.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

MAX_LEDGER = 16 * 1024 * 1024
IDENTITY = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
BUCKET = re.compile(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]\Z")
SEQUENCE = re.compile(r"([0-9]{20})\.json\Z")


class CloudStoreError(RuntimeError):
    pass


def token():
    request = Request('http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token',
                      headers={'Metadata-Flavor': 'Google'})
    with urlopen(request, timeout=10) as response:
        payload = json.load(response)
    return payload['access_token']


def _bucket(value):
    if not isinstance(value, str) or not BUCKET.fullmatch(value):
        raise CloudStoreError('Invalid private bucket name')
    return value


class GCS:
    def request(self, url, *, method='GET', data=None, headers=None):
        return urlopen(Request(url, method=method, data=data,
                              headers={'Authorization': 'Bearer ' + token(), **(headers or {})}), timeout=50)

    def list(self, bucket, prefix):
        _bucket(bucket)
        page = None
        rows = []
        while True:
            params = {'prefix': prefix, 'maxResults': 1000, 'fields': 'items(name,size),nextPageToken'}
            if page:
                params['pageToken'] = page
            with self.request('https://storage.googleapis.com/storage/v1/b/' + bucket + '/o?' + urlencode(params)) as response:
                result = json.load(response)
            rows.extend(result.get('items', []))
            if len(rows) > 100_000:
                raise CloudStoreError('Ledger listing requires an operator-managed archival upgrade')
            page = result.get('nextPageToken')
            if not page:
                return rows

    def read(self, bucket, name, maximum=MAX_LEDGER):
        _bucket(bucket)
        url = 'https://storage.googleapis.com/storage/v1/b/' + bucket + '/o/' + quote(name, safe='') + '?alt=media'
        with self.request(url) as response:
            raw = response.read(maximum + 1)
        if len(raw) > maximum:
            raise CloudStoreError('Remote ledger exceeds the verified size bound')
        return raw

    def write_once(self, bucket, name, raw):
        _bucket(bucket)
        url = 'https://storage.googleapis.com/upload/storage/v1/b/' + bucket + '/o?' + urlencode({
            'uploadType': 'media', 'name': name, 'ifGenerationMatch': 0})
        try:
            with self.request(url, method='POST', data=raw, headers={'Content-Type': 'application/json'}) as response:
                json.load(response)
            return True
        except HTTPError as error:
            if error.code in {409, 412}:
                return False
            raise

    def upload_backup(self, bucket, path):
        """Streaming resumable upload, bounded memory and unique hash identity."""
        _bucket(bucket)
        digest = file_hash(path)
        md5 = hashlib.md5(usedforsecurity=False)
        with path.open('rb') as source:
            while raw := source.read(1024 * 1024):
                md5.update(raw)
        expected_md5 = base64.b64encode(md5.digest()).decode()
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        name = f'backups/{stamp}-{digest}.tar.zst'
        url = 'https://storage.googleapis.com/upload/storage/v1/b/' + bucket + '/o?' + urlencode({
            'uploadType': 'resumable', 'name': name, 'ifGenerationMatch': 0})
        size = path.stat().st_size
        with self.request(url, method='POST', data=b'', headers={
                'X-Upload-Content-Length': str(size), 'X-Upload-Content-Type': 'application/zstd'}) as response:
            upload_url = response.headers['Location']
        if not upload_url.startswith('https://storage.googleapis.com/'):
            raise CloudStoreError('Unexpected GCS resumable endpoint')
        chunk_size = 8 * 1024 * 1024
        offset = 0
        with path.open('rb') as source:
            while raw := source.read(chunk_size):
                last = offset + len(raw) - 1
                request = Request(upload_url, method='PUT', data=raw, headers={
                    'Content-Length': str(len(raw)), 'Content-Range': f'bytes {offset}-{last}/{size}'})
                try:
                    with urlopen(request, timeout=120) as response:
                        if last + 1 != size or response.status not in {200, 201}:
                            raise CloudStoreError('Unexpected GCS upload completion')
                        metadata = json.load(response)
                        if (int(metadata['size']) != size or metadata['name'] != name
                                or metadata.get('md5Hash') != expected_md5):
                            raise CloudStoreError('Uploaded backup identity/length mismatch')
                except HTTPError as error:
                    if error.code != 308 or last + 1 == size:
                        raise
                    received = error.headers.get('Range', '')
                    if received != f'bytes=0-{last}':
                        raise CloudStoreError('GCS upload checkpoint mismatch') from None
                offset = last + 1
        if offset != size or not size:
            raise CloudStoreError('Empty or incomplete backup upload')
        # Resumable session URLs are bearer capabilities. Never print/store them.
        return {'object': name, 'bytes': size, 'sha256': digest}

    def download_backup(self, bucket, name, destination):
        _bucket(bucket)
        if not re.fullmatch(r'backups/[0-9TZ]+-([a-f0-9]{64})\.tar\.zst', name):
            raise CloudStoreError('Choose a hash-identified backup object from this recipe')
        expected = re.search(r'-([a-f0-9]{64})\.tar\.zst\Z', name).group(1)
        url = 'https://storage.googleapis.com/storage/v1/b/' + bucket + '/o/' + quote(name, safe='')
        with self.request(url + '?fields=size') as response:
            size = int(json.load(response)['size'])
        if shutil.disk_usage(destination.parent).free < size + 1024**3:
            raise CloudStoreError('Insufficient restore scratch space')
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as target, self.request(url + '?alt=media') as source:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            if destination.stat().st_size != size or file_hash(destination) != expected:
                raise CloudStoreError('Backup hash/length verification failed')
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while raw := source.read(1024 * 1024):
            digest.update(raw)
    return digest.hexdigest()


def decode_ledger(raw, identity):
    if len(raw) > MAX_LEDGER:
        raise CloudStoreError('Deletion ledger exceeds the supported size')
    try:
        envelope = json.loads(raw)
        payload = envelope['payload']
        valid = (set(envelope) == {'payload', 'signature'} and payload['installation_id'] == identity
                 and type(payload['sequence']) is int and 0 <= payload['sequence'] <= 99_999
                 and isinstance(payload['events'], list) and len(payload['events']) == payload['sequence']
                 and isinstance(envelope['signature'], str) and re.fullmatch(r'[a-f0-9]{64}', envelope['signature']))
        if not valid:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise CloudStoreError('Invalid deletion-ledger envelope or installation identity') from None
    # The application verifies the HMAC and full event schema. The transport
    # additionally enforces immutable sequence/history, and never possesses a
    # separate authority to rewrite a published object.
    return payload


def latest(storage, bucket, identity):
    if not IDENTITY.fullmatch(identity):
        raise CloudStoreError('Invalid stable installation identity')
    prefix = 'ledgers/' + identity + '/'
    candidates = []
    for row in storage.list(bucket, prefix):
        suffix = row['name'].removeprefix(prefix)
        match = SEQUENCE.fullmatch(suffix)
        if not match:
            raise CloudStoreError('Unexpected object in the authoritative ledger namespace')
        candidates.append((int(match.group(1)), row['name']))
    if not candidates:
        return None
    sequence, name = max(candidates)
    raw = storage.read(bucket, name)
    payload = decode_ledger(raw, identity)
    if payload['sequence'] != sequence:
        raise CloudStoreError('Ledger object identity does not match its sequence')
    return raw, payload


def publish_ledger(raw, storage, bucket, identity):
    if not IDENTITY.fullmatch(identity):
        raise CloudStoreError('Invalid stable installation identity')
    incoming = decode_ledger(raw, identity)
    previous = latest(storage, bucket, identity)
    if previous:
        old_raw, old = previous
        if incoming['sequence'] < old['sequence']:
            raise CloudStoreError('Deletion-ledger rollback rejected')
        if incoming['events'][:old['sequence']] != old['events']:
            raise CloudStoreError('Deletion-ledger history conflict rejected')
        if incoming['sequence'] == old['sequence']:
            if raw != old_raw:
                raise CloudStoreError('Same-sequence ledger content conflict rejected')
            return {'sequence': incoming['sequence'], 'duplicate': True}
        if incoming['sequence'] != old['sequence'] + 1:
            raise CloudStoreError('Publish every deletion-ledger sequence without gaps')
    elif incoming['sequence'] != 0:
        raise CloudStoreError('Initialize the authoritative ledger at sequence zero before publication')
    name = f"ledgers/{identity}/{incoming['sequence']:020d}.json"
    if not storage.write_once(bucket, name, raw):
        if storage.read(bucket, name) != raw:
            raise CloudStoreError('Concurrent same-sequence publication conflict rejected')
    # Re-read the committed object. Exit success only after durable publication.
    if storage.read(bucket, name) != raw:
        raise CloudStoreError('Committed deletion-ledger bytes did not verify')
    committed = latest(storage, bucket, identity)
    if committed is None or committed[0] != raw:
        raise CloudStoreError('Authoritative deletion ledger advanced; retry from its current sequence')
    return {'sequence': incoming['sequence'], 'duplicate': False}


def private_write(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.doxa-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def merge_ledger(remote_raw, destination, identity):
    """Preserve an unpublished local intent; reject conflicting histories.

    The application verifies signatures and republishes the selected current
    ledger before serving traffic. Bootstrap never discards a newer local write
    merely because its earlier synchronous cloud publication failed.
    """
    remote = decode_ledger(remote_raw, identity)
    if destination.exists():
        with destination.open('rb') as source:
            local_raw = source.read(MAX_LEDGER + 1)
        local = decode_ledger(local_raw, identity)
        overlap = min(local['sequence'], remote['sequence'])
        if local['events'][:overlap] != remote['events'][:overlap]:
            raise CloudStoreError('Local and remote deletion-ledger histories conflict')
        if local['sequence'] == remote['sequence']:
            if local_raw != remote_raw:
                raise CloudStoreError('Local and remote same-sequence deletion ledgers differ')
            return 'same'
        if local['sequence'] > remote['sequence']:
            return 'kept_newer_local'
    private_write(destination, remote_raw)
    return 'recovered_newer_remote'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('publish-ledger')
    item = actions.add_parser('latest-ledger')
    item.add_argument('bucket')
    item.add_argument('installation_id')
    item.add_argument('destination', type=Path)
    item = actions.add_parser('merge-ledger')
    item.add_argument('remote', type=Path)
    item.add_argument('destination', type=Path)
    item.add_argument('installation_id')
    item = actions.add_parser('secret')
    item.add_argument('project')
    item.add_argument('secret_id')
    item.add_argument('destination', type=Path)
    item = actions.add_parser('backup-upload')
    item.add_argument('bucket')
    item.add_argument('path', type=Path)
    item = actions.add_parser('backup-download')
    item.add_argument('bucket')
    item.add_argument('object')
    item.add_argument('destination', type=Path)
    item = actions.add_parser('installation-id')
    item.add_argument('environment', type=Path)
    args = parser.parse_args(argv)
    storage = GCS()
    try:
        if args.action == 'publish-ledger':
            raw = sys.stdin.buffer.read(MAX_LEDGER + 1)
            result = publish_ledger(raw, storage, os.environ['DOXA_LEDGER_BUCKET'], os.environ['DOXA_INSTALLATION_ID'])
            print(json.dumps(result, sort_keys=True))
        elif args.action == 'latest-ledger':
            result = latest(storage, args.bucket, args.installation_id)
            if result is None:
                return 3
            private_write(args.destination, result[0])
        elif args.action == 'merge-ledger':
            with args.remote.open('rb') as source:
                raw = source.read(MAX_LEDGER + 1)
            merge_ledger(raw, args.destination, args.installation_id)
        elif args.action == 'secret':
            if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', args.project) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', args.secret_id):
                raise CloudStoreError('Invalid Secret Manager resource identity')
            url = f'https://secretmanager.googleapis.com/v1/projects/{args.project}/secrets/{args.secret_id}/versions/latest:access'
            with storage.request(url) as response:
                encoded = json.load(response)['payload']['data']
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > 512 * 1024:
                raise CloudStoreError('Private provisioning secret exceeds the supported size')
            private_write(args.destination, raw)
        elif args.action == 'backup-upload':
            print(json.dumps(storage.upload_backup(args.bucket, args.path), sort_keys=True))
        elif args.action == 'backup-download':
            storage.download_backup(args.bucket, args.object, args.destination)
        elif args.action == 'installation-id':
            import shlex
            values = []
            for line in args.environment.read_text().splitlines():
                if line.startswith('DOXA_INSTALLATION_ID='):
                    parts = shlex.split(line.split('=', 1)[1], comments=True)
                    if len(parts) == 1 and IDENTITY.fullmatch(parts[0]):
                        values.append(parts[0])
            if len(values) != 1:
                raise CloudStoreError('Private environment needs one stable installation identity')
            print(values[0])
        return 0
    except (CloudStoreError, HTTPError, OSError, ValueError, KeyError):
        # Never print response bodies, private environment data or upload URLs.
        print('Cloud operation failed; inspect permissions, connectivity and the declared installation configuration. No document or credential content is logged.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
