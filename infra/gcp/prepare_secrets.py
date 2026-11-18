#!/usr/bin/env python3
"""Prepare a NEW private Secret Manager upload directory, without printing keys.

Existing vault/ledger keys are preserved. New installation identity/ledger keys
require --new-installation. This creates local files only, never cloud resources.
"""
from __future__ import annotations

import argparse
import base64
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values  # noqa: E402
from doxa.access import AccessControl  # noqa: E402


def private_read(path):
    if not path.is_absolute() or path != path.resolve():
        raise ValueError('Use absolute, non-symlink private input paths')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid()
                or info.st_size > 512 * 1024):
            raise ValueError('Input files must be private, operator-owned regular files')
        return source.read(512 * 1024 + 1)


def key32(value):
    if not isinstance(value, str) or len(base64.b64decode(value, validate=True)) != 32:
        raise ValueError('Retain a valid existing 32-byte encryption/signing key')
    return value


def _inside_checkout(path):
    return any((parent / '.git').exists() for parent in (path, *path.parents))


def validate_pricing(raw):
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {'version', 'revision', 'rates'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['revision'], str) or not 1 <= len(value['revision']) <= 128
            or not isinstance(value['rates'], list) or len(value['rates']) > 128):
        raise ValueError('Pricing requires a versioned server-owned rates list')
    keys = set()
    for row in value['rates']:
        if not isinstance(row, dict) or set(row) != {'provider', 'model', 'endpoint', 'input_price_per_million', 'output_price_per_million'}:
            raise ValueError('Pricing entry contains unsupported fields')
        if row['provider'] not in {'openai', 'openai-compatible', 'vllm', 'cloudflare', 'runpod'}:
            raise ValueError('Choose an explicitly metered supported remote provider')
        identity = (row['provider'], row['model'], row['endpoint'])
        if any(not isinstance(item, str) or not item or len(item) > 2048 for item in identity) or identity in keys:
            raise ValueError('Pricing identities must be unique nonempty strings')
        endpoint = urlsplit(row['endpoint'])
        if endpoint.scheme != 'https' or not endpoint.netloc or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError('Pricing must bind the exact credential-free HTTPS endpoint')
        keys.add(identity)
        for field in ('input_price_per_million', 'output_price_per_million'):
            rate = row[field]
            if type(rate) not in {int, float} or not Decimal(str(rate)).is_finite() or not 0 < rate <= 1_000_000:
                raise ValueError('Remote token rates must be positive finite numbers')


def prepare(source, output, pricing, operator_ids, *, new_installation=False):
    source = Path(source).expanduser().absolute()
    output = Path(output).expanduser().absolute()
    pricing = Path(pricing).expanduser().absolute()
    if os.name != 'posix':
        raise ValueError('Dedicated native pilot provisioning requires POSIX')
    if output != output.resolve() or _inside_checkout(output):
        raise ValueError('Choose a new private output directory outside source checkouts')
    if output.exists():
        raise ValueError('Output directory must be new; secrets are never overwritten')
    access_raw = private_read(source / 'access.json')
    control = AccessControl('managed', str(source / 'access.json'))
    people = {row['principal'].id: row['principal'] for row in control.configuration()['users']}
    if not operator_ids or any(identity not in people or 'admin' not in people[identity].permissions for identity in operator_ids):
        raise ValueError('Service operators must be existing administrator identities')
    environment_raw = private_read(source / 'pilot.env')
    import io
    environment = dict(dotenv_values(stream=io.StringIO(environment_raw.decode()), interpolate=False))
    key32(environment.get('DOXA_PRIVACY_VAULT_KEY'))
    if bool(environment.get('DOXA_DELETION_LEDGER_KEY')) != bool(environment.get('DOXA_INSTALLATION_ID')):
        raise ValueError('Existing installation identity and ledger key must be retained together')
    if environment.get('DOXA_DELETION_LEDGER_KEY'):
        key32(environment['DOXA_DELETION_LEDGER_KEY'])
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', environment['DOXA_INSTALLATION_ID']):
            raise ValueError('Retain a valid stable installation identity')
        if new_installation:
            raise ValueError('An existing installation identity cannot be silently replaced')
    elif new_installation:
        environment['DOXA_INSTALLATION_ID'] = uuid.uuid4().hex
        environment['DOXA_DELETION_LEDGER_KEY'] = base64.b64encode(secrets.token_bytes(32)).decode()
    else:
        raise ValueError('New installations require explicit --new-installation; restores require their previous keys')
    pricing_raw = private_read(pricing)
    validate_pricing(pricing_raw)
    environment.update({'DOXA_ACCESS_MODE': 'managed', 'DOXA_ACCESS_CONFIG_FILE': '/run/secrets/access.json',
        'DOXA_COMMERCE_RATES_FILE': '/run/secrets/pricing.json', 'DOXA_REQUIRE_ACCEPTED_QUOTE': 'true',
        'DOXA_COMMERCE_OPERATOR_IDS': ','.join(sorted(set(operator_ids))),
        'DOXA_DELETION_LEDGER_PATH': '/control/deletions/ledger.json'})
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, raw in [('access.json', access_raw), ('pricing.json', pricing_raw),
                      ('pilot.env', ''.join(f'{key}={json.dumps(value)}\n' for key, value in sorted(environment.items()) if value is not None).encode())]:
        descriptor = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(raw)
    return {'directory': str(output), 'files': ['access.json', 'pilot.env', 'pricing.json'],
            'cloud_resources_created': False, 'credentials_printed': False,
            'installation_identity_created': new_installation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-directory', required=True, type=Path)
    parser.add_argument('--output-directory', required=True, type=Path)
    parser.add_argument('--pricing-file', required=True, type=Path)
    parser.add_argument('--operator-id', action='append', required=True)
    parser.add_argument('--new-installation', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source_directory, args.output_directory, args.pricing_file,
              args.operator_id, new_installation=args.new_installation), sort_keys=True))
    except (ValueError, OSError, InvalidOperation):
        parser.error('Private preparation failed. Check file permissions, administrator identities, pricing and explicit new-versus-restored installation settings; no secret contents are printed.')


if __name__ == '__main__':
    main()
