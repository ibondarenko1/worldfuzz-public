# Install and upgrade the 0.4.0rc2 release candidate

WorldFuzz 0.4.0rc2 requires Python 3.11 or newer. Install a locally supplied,
hash-checked wheel in an isolated environment:

```text
python -m venv worldfuzz-runtime
worldfuzz-runtime/Scripts/python -m pip install worldfuzz-0.4.0rc2-py3-none-any.whl
worldfuzz-runtime/Scripts/worldfuzz --help
```

On POSIX systems the executable is `worldfuzz-runtime/bin/worldfuzz`. Optional
features remain explicit extras. Installing the base wheel does not load a
credential, call a provider or create a workspace.

The installed package contains the UI, runtime schemas, PostgreSQL migrations,
published Moving AI input/example metadata, and third-party notices. Contract
snapshots and deployable support files are installed below
`<environment>/share/worldfuzz`. The authoritative runtime schemas remain the
validated Python schemas exposed by `get_capabilities` and OpenAPI.

## Upgrade a workspace

Never test an upgrade against the only workspace copy. With the old WorldFuzz
runtime, first create a consistent backup:

```text
worldfuzz platform --store OLD/catalog.sqlite3 --root OLD/results \
  workspace-backup --destination BACKUPS/before-0.4.0rc2
```

Restore it to a new destination with the candidate runtime:

```text
worldfuzz platform workspace-restore \
  --backup BACKUPS/before-0.4.0rc2 --destination RESTORED
```

For an organization-bound workspace, use the authenticated `create_backup`
and `restore_backup` operations. The restore receives a new workspace ID and
a fresh scoped token; it preserves record IDs, source rights, grant restrictions
and revocations. Verify the restore receipt and inspect saved results before
switching clients. Do not point two service versions at one live SQLite file.

The supported rollback for this candidate is recovery from the pre-upgrade
backup into another new destination using the version that created that backup.
Downgrading an in-place catalog, opening a newer catalog with an older runtime,
and restoring a 0.4 backup with 0.3 are not claimed compatible unless separately
tested. Keep the backup and its manifest until the candidate has been accepted.

## Kali/Linux container acceptance

WP33 is **ACCEPTED_TESTED_SCOPE** for the matrix in the
[rc2 receipt](../examples/wp33-container-rc2-20260913/receipt.json). The exact rc2
archives were built from release source `63066b7`; they remain frozen, including
the pre-acceptance installation instructions inside the sdist. This repository
page and the receipt now record the subsequent observed container result.

A separate Kali/Linux x86_64 instance built and installed the selected 0.4.0rc2
wheel, served UI assets, enforced scoped organization access, completed published
`movingai:0` through the common HTTP/runner/verifier path, and retained the result
and artifact hashes after recreation from the same image. The original rc1
artifacts and native/upgrade evidence remain separate. All runtime files except
the version constant and all shared payloads are byte-identical between wheels.
This supports unchanged behavior without claiming a repeated rc2 Windows install.

The build context contains only the selected wheel and the needed `dist/`
directory. Docker receives its Dockerfile and ignore file as control inputs; see
[Docker build context rules](https://docs.docker.com/build/concepts/context/#dockerignore-files).
The full wheel basename is retained for pip, and the image saves the input SHA-256
in `/opt/worldfuzz/installed-wheel.sha256` before deleting the temporary wheel.

Use a **new** operator-owned `wp33-rc2-SUFFIX` directory on an authorized Linux
host. Copy `.dockerignore`, `deployment/Dockerfile`, `deployment/compose.yaml` and
`dist/worldfuzz-0.4.0rc2-py3-none-any.whl`, preserving paths. Keep the existing
`evaluation/wp33_release.py` outside the context. Do not reuse a working authority
or source corpus, start Docker/WSL on Windows, or create `private/` beforehand.

```sh
set -eu
umask 077
export COMPOSE_PROJECT_NAME="worldfuzz-$(basename "$PWD" | tr '[:upper:]' '[:lower:]')"
export WORLDFUZZ_PRIVATE_DIR="$PWD/private"
printf '39b31df1af4b7c2e5e05bb26a5057454548a02661be136b7d801846ef2302a1e  dist/worldfuzz-0.4.0rc2-py3-none-any.whl\n' | sha256sum -c -
timeout --signal=TERM --kill-after=15s 600 docker compose --parallel 1 -f deployment/compose.yaml build
timeout --signal=TERM --kill-after=45s 240 python3 /path/to/evaluation/wp33_release.py container-host \
  --workspace "$PWD" --version 0.4.0rc2 \
  --wheel-sha256 39b31df1af4b7c2e5e05bb26a5057454548a02661be136b7d801846ef2302a1e
```

The supervisor uses the operator's existing delegated Docker access. Its bounded
initializer changes only the newly created mode-0700 private bind to UID/GID
10001. It has no network, a read-only root, only the CHOWN capability, and no
unrelated host mounts. No sudo policy, daemon option, privilege mode or group/world
permission changes are needed. Container-visible workspace paths are fixed at
`/private/identity.sqlite3`, `/private/catalog.sqlite3` and `/private/results`.
A temporary random password exists only in process memory/environment, and scoped
tokens are revoked after each pass. Do not enable shell tracing or log credentials.

The unchanged Compose service retains UID/GID 10001, read-only root, 128 MiB tmpfs,
3 GiB memory, 1 CPU, 32 PIDs, init, no restart policy and host networking with the
HTTP server bound to loopback. Actual inspect/cgroup values and the Linux runner
probe are in the receipt. Rootless or user-namespace-remapped ownership has not
been accepted: provision the mapped owner through an authorized operator before
attempting that placement; never broaden mode 0700 or change daemon policy.

The bounded check saves `first.json`, `resume.json`, `supervisor.json` and downloaded
published artifacts in the isolated context. It records old/new container IDs,
image ID, wheel digest/version, resource enforcement, HTTP/auth outcomes, worker
PID/exit and persistence, then stops only its own Compose project. It preserves
the private store and local image; no prune, volume deletion or publishing occurs.
A timeout is an incomplete acceptance, not a robotics-task failure or permission
to repeat the task with a new idempotency key. The published plan uses one case,
30 seconds wall time, 20 CPU seconds, 256 MiB worker address space and 1 MiB artifacts.

This container result does not extend the retained Windows matrix, independent
agent research or physical-replay scope. WP34 is next; it has not started.
