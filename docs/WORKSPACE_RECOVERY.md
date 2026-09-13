# Local workspace backup and recovery

Backups now include the SQLite catalog and every artifact referenced by stored
run results. This preserves plans, jobs, events, datasets, episodes, registered
sensor assets, route files and dataset exports. It extends the earlier database-
only snapshot. Raw files that exist only outside this catalog, package installs,
credentials and unregistered files are not collected.

## Commands

```sh
worldfuzz platform --store artifacts/corpus.sqlite3 --root artifacts/platform \
  workspace-backup --destination backups/checkpoint-001 --max-mib 1024

worldfuzz platform workspace-restore --backup backups/checkpoint-001 \
  --destination artifacts/recovered-001 --max-mib 1024

worldfuzz platform --store artifacts/recovered-001/catalog.sqlite3 \
  --root artifacts/recovered-001/results serve
```

Both commands produce structured JSON. They are local maintenance commands,
not remotely exposed filesystem operations. Destinations must be new; neither
command overwrites an existing backup or workspace. Use the same compatible
WorldFuzz version and installed adapter environment when starting restored work.
Old completed evidence remains readable without re-executing its procedures.

## Consistency and verification

Backup requires every recorded job to be terminal. Queued, running or cancellation-
requested jobs block it, including stale jobs that still need reconciliation.
The operation holds a SQLite write reservation while taking the database snapshot
and copying registered files. New jobs and catalog writers wait until it ends.
This is a maintenance operation, not zero-downtime continuous replication.

Each artifact is checked against its database size and SHA-256 while streaming.
Missing files, changed bytes, linked paths, duplicate references and mismatched
workspace roots cause failure. Limits default to 1 GiB and 10,000 result files.
The CLI supports 1–65,536 MiB; the file-count cap stays fixed. Allowlist-free scans
of the filesystem are not used: only database-referenced result files are copied.

The manifest records file hashes, catalog fingerprints and logical database rows.
Restoration verifies both file inventory and logical rows. It changes only the
artifact-root binding to the new result directory. Episode IDs, payloads, group
splits, job IDs, idempotency keys, result hashes and relative artifact references
are retained. Restoration never restarts a job. It does not switch the running
service automatically; the returned paths identify the recovered workspace.

A completion manifest/receipt is published last. Interrupted or incomplete output
must not be treated as a completed backup/restore. Existing destinations remain
protected on retry. Content hashes detect corruption, not malicious replacement
of both a manifest and its contents. Backups are intended for the owner's trusted
local files. Encryption, remote storage, power-loss durability certification,
automated retention and PostgreSQL recovery remain outside this checkpoint.

## Recorded acceptance

The test restored the prior real 209-episode catalog, executed one included
published navigation task in an isolated working copy, exported its navigation
records and backed up/restored that workspace again. All 209 prior episodes plus
one new verified route were retained. Three referenced files (route, JSONL export
and dataset manifest) matched byte-for-byte. Result metadata remained equal and
an idempotent repeat returned the original job ID without executing again.

```sh
python scripts/verify-workspace-recovery.py \
  --source-db PATH_TO_209_EPISODE_PLATFORM_CATALOG \
  --source-root ITS_REGISTERED_ARTIFACT_ROOT \
  --out artifacts/new-recovery-acceptance

python -m unittest discover -s tests -p test_workspace_backup.py -v
```

The source for this recording was the reconstructed real-data catalog from the
previous migration checkpoint, not a separate user's production database.
[Receipts and retained artifact hashes](../examples/workspace-recovery-20260910/README.md).
