# Retention and deletion-safe recovery

DOXA v0.6 adds explicitly enabled automatic **whole-project** retention and a signed deletion ledger outside ordinary customer-data backups. Local operation keeps the existing manual defaults. This extends live erasure; it does not claim physical-media wiping, guaranteed anonymity, provider-side deletion or automatic expiry of customer downloads.

## What changes, and what remains separate

An erasure request first records an opaque project ID, request digest and timestamp in the external ledger. It then creates the existing durable erasure barrier. Uploads, analysis, indexing and reports must finish or release their actual worker leases before inventory/deletion proceeds. Completion enters the external ledger only after live database/artifact erasure has committed. Failures remain failed or waiting rather than receiving a success acknowledgement.

The ledger contains no source text, project names, author names, filenames, raw idempotency keys or credentials. It is signed with HMAC-SHA-256 and scoped to a stable installation identity. A private SQLite checkpoint detects a ledger older than the current database. Signatures detect corruption; an operator controlling the signing key and every ledger copy can still replace history. A signature is not remote attestation or protection from a malicious infrastructure administrator.

Newly completed live-erasure requests also drop their stored actor identity and replace the raw idempotency key with its SHA-256 digest. Original-key retries still resolve to the same receipt. Older completed records and published historical receipts are not silently rewritten. Opaque project/job identities and aggregate deletion counts remain as necessary recovery metadata; hashing does not constitute a guarantee of anonymity.

| Storage | Handling |
| --- | --- |
| Live native database and owned artifacts | Inventoried and erased by the existing resumable project job |
| External deletion ledger | Retained separately so older customer-data backups cannot erase deletion obligations |
| Ordinary backups | Expire under a separate operator/cloud policy; live erasure does not remove them |
| Inference-provider copies | Governed by the approved provider contract and separately recorded retention |
| Downloaded files or physical media | Outside the application erasure operation |

## Provision once, before confidential use

Configure a private, operator-owned `0700` directory outside the data directory and its backups. The ledger and lock files use `0600`. Symlinks and permissive permissions are rejected. The native installation needs POSIX no-follow file operations.

Keep these values in the private operator environment or approved secret manager:

```text
DOXA_INSTALLATION_ID=<stable opaque installation ID, at least eight characters>
DOXA_DELETION_LEDGER_PATH=/var/lib/doxa-control/deletions/ledger.json
DOXA_DELETION_LEDGER_KEY=<base64 encoding of 32 cryptographically random bytes>
DOXA_AUTOMATIC_RETENTION=false
```

Generate and store the key privately; do not print it into shell history, repository files, deployment logs or reports. Keep this signing key separate from the identity-vault encryption key. Preserve the same installation ID and signing key across backup recovery. Do not copy one customer's configuration into another customer's installation.

From the repository, with the server stopped and the private directory provisioned:

```sh
.venv/bin/python scripts/privacy_lifecycle_v06.py init-ledger \
  --data-dir /data --env-file /private/operator/pilot.env
.venv/bin/python scripts/privacy_lifecycle_v06.py restore-check \
  --data-dir /data --env-file /private/operator/pilot.env
```

Initialization refuses to replace an existing ledger. It imports deletion history still present in the current database; it cannot reconstruct erasures missing from both that database and all external ledger copies. A missing configured ledger blocks startup and deletion rather than silently creating an empty replacement.

### Durable cloud publication

For disaster recovery beyond a surviving external control disk, configure `DOXA_DELETION_LEDGER_PUBLISH_ARGV` as a JSON array containing an absolute executable path and fixed arguments. The publisher receives the canonical signed ledger on standard input. DOXA uses no shell, suppresses command output, excludes the signing key and inference credentials from the child environment, and applies a 60-second deadline. Only a zero exit code acknowledges publication.

The publisher must commit to durable storage before exiting, be idempotent, reject conflicting bytes at one sequence and prevent rollback. Immutable sequence/digest objects in a separate private control bucket are one implementation. Restore the highest authoritative verified sequence before starting a recovered customer installation. A periodic mirror alone has a recovery-point gap: losing the local control disk before its next mirror can lose acknowledged obligations. Do not advertise zero-loss deletion recovery from that arrangement.

Local ledger state is atomically persisted before publication. If publication fails, the operation fails closed; a later verified read retries publication before permitting erasure or recovery. A provider failure therefore cannot be bypassed by repeating the same request. The application still relies on the configured publisher correctly implementing its durable-commit contract.

## Enable automatic retention deliberately

Automatic expiry requires **both**:

1. The operator sets `DOXA_AUTOMATIC_RETENTION=true` and provisions a working external ledger.
2. The project policy explicitly sets `retention_enabled=true`.

The current policy uses the **last saved policy timestamp** as its anchor. Whole-project expiry is the earlier of raw and derived retention deadlines. It does not promise independent raw-only/derived-only deletion or automatically infer a final-delivery date. Saving a policy again resets that documented anchor; operators promising a delivery-based retention period must save the policy at delivery and preserve that event separately.

The periodic scheduler processes a bounded number of projects and objects per tick. Expiry requests are idempotent. Active writers keep the project waiting; no lease is silently expired merely to complete erasure. Project dates remain review candidates when the installation-wide switch is off.

An operator can advance a bounded tick explicitly:

```sh
.venv/bin/python scripts/privacy_lifecycle_v06.py retention-tick \
  --data-dir /data --env-file /private/operator/pilot.env
```

## Recover a backup without resurrecting deleted content

Stop public traffic and all old writers. Restore a coherent database **and object** backup into the customer data directory. Recover the latest external ledger separately, using its original installation identity and signing key. Run `restore-check` before enabling traffic; the server startup guard performs the same check.

The guard reapplies every external deletion obligation, including requests whose database job never committed before a crash. A restored database's old completion flag is not trusted while project data remains. Missing/corrupt/wrong-installation ledgers, active writers, unexplained private artifacts and exhausted replay bounds block readiness. Resume recovery or obtain a coherent backup; do not bypass the guard or guess ownership.

After success, original and derived content for the erased project is absent from the served installation. Other projects remain intact. The ordinary backup itself still contains its old copy until its separately managed expiry. Run `restore-check` on a new machine as part of a real recovery drill; unit tests are not evidence that an operator's cloud backup policy works.

## Track external expiry honestly

`record-expiry` records a backup/provider deadline against an existing deletion obligation. Only a SHA-256 reference enters the ledger. An optional `--attest-expired` records an operator assertion after the deadline; it does not independently verify the external system.

```sh
.venv/bin/python scripts/privacy_lifecycle_v06.py record-expiry \
  --data-dir /data --env-file /private/operator/pilot.env \
  --project-id PROJECT_ID --target backup --reference OPAQUE_BACKUP_ID \
  --expires-at 2027-01-01T00:00:00+00:00
```

Keep actual backup inventory/deletion evidence and provider contractual evidence separately. Live-erasure receipts continue to say `expiry_verified: false`; the presence of a deadline or operator attestation must not turn into an automatic claim that all copies were deleted.

## Verification

`tests/test_retention_v06.py` exercises real signed-ledger initialization, invalid permissions/signatures/installation/key/sequence, policy opt-ins, active-work draining, crashes before job commit, failure after live deletion, synchronous-publisher failure/retry, excluded child-process credentials, restoration of a pre-deletion backup and separate-project preservation. These are isolated software tests; no customer material, real cloud resources or paid inference is involved.

See [native pilot operation](managed-pilots-v05.md) for source access, pseudonymization and erasure boundaries, and [commercial readiness](commercial-service-roadmap.md) for the additional hosted security, legal and service gates.
