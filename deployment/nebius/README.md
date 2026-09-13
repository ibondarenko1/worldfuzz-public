# Proposed NVIDIA → Nebius Job → independently checked navigation

This optional recipe consumes the installed `worldfuzz==0.4.0rc6` and its existing
contracts. It is not a new adapter loader, cloud platform or alternative runner.
Preparation and mocked transport checks are complete; real inference, image
build/upload and Nebius execution are **pending approval and account access**.

The model selects A* or Dijkstra for the exact published `movingai:8` task using
the source's grid/endpoints/reference length and fixed computational objective.
This is a useful, inspectable solver-configuration decision, not a claim that an
LLM is needed for shortest paths or improves the solver. No scenario, map, source
right, budget or criterion is invented by the model. Invalid output stops the
process without substituting a fixture. The common service rechecks the proposal
inside the actual job, starts its existing separate worker and independent route
checker, and returns the ordinary report/artifacts. Model and task costs remain
separate; a cloud bill is not synthesized from the local BudgetReservation ledger.

## Offline preparation (no inference or Jobs)

Use an existing environment with the hash-checked rc6 wheel, outside its checkout:

```sh
python competition.py prepare --workspace /private/worldfuzz/competition-prepare
```

This creates a fresh isolated empty workspace solely for the scoped resource
query, revokes its temporary token and saves request.json. No 209-episode corpus
is opened or restored. The supplied frozen-request.json contains that same public
request. It is not an approved execution or a model response. Do not edit it after
approval; compare its SHA-256 with the release manifest.

## Owner gates before a live run

Read [the $1 proposal, assumptions and stop procedure](../../docs/NEBIUS_COMPETITION_BUDGET.md).
Finish existing AI Cloud login; verify project/subnet, preset availability, Jobs
permission, billing and credit expiry by read-only queries. Confirm the detailed
Token Factory rate and which organization owns the existing DPAPI key. Confirm
Jobs maintenance/restart policy and result log retention fit the allowance.

Copy authorization.example.json to a private directory. Only after approval fill
the exact SHA-256 of frozen-request.json, confirm rates/project and set approved
true. This file is a local operator acknowledgement, not a cloud credential or a
cryptographic proof of user consent. A local filesystem owner remains trusted.
Neither importing this module nor `prepare` sends a completion.

For the authorized Windows provider call, `propose.ps1` loads the existing DPAPI
key only into the child environment and clears it afterward. Supply the already
installed Python executable; it does not install anything:

```powershell
.\propose.ps1 -PythonPath C:\approved-env\Scripts\python.exe -RequestPath .\frozen-request.json -AuthorizationPath C:\private\allowance.json -OutputDirectory C:\private\inference-001
```

Other authorized hosts can use existing `NEBIUS_API_KEY` in protected process
environment and `python competition.py propose --request frozen-request.json
--authorization /private/allowance.json --out /private/inference-001`.
There is one request, 512 completion tokens, 30-second transport timeout and no
retry. An exclusive inference-attempt.json is written before transport. Unknown
delivery/billing stops the allowance; do not create another directory to retry.
The response is schema-validated and recorded with provider usage before any job.

## Container and job (commands prepared, not executed)

On approved Kali/Linux only, resolve the Linux/amd64 `python:3.14.3-slim` digest,
build the supplied Dockerfile with `BASE_IMAGE` and `WHEEL_SHA256`, and record the
image ID, installed-packages.txt and installed-wheel.sha256. Explicitly approve
image upload to a selected accessible registry; no registry is provisioned here.
Build context consists only of Dockerfile, competition.py and the selected wheel.
It contains neither credentials nor private corpus. UID 10001 writes to its own
new `/tmp/worldfuzz-job`; no existing private directory permissions are weakened.

After the image is uploaded and pinned, render the documented REST payload:

```sh
python render_job.py --image REGISTRY/IMAGE@sha256:ACTUAL_DIGEST --project ACTUAL_PROJECT_ID --subnet ACTUAL_SUBNET_ID --input /private/inference-001/approved-input.json --out /private/job.json
```

These are operator placeholders, not fabricated available resources. The
renderer never sends a request. The injected input is ≤64 KiB and hash-pinned.
No model key is sent to the container. Use the existing authenticated Nebius
REST session to POST **once** to `https://api.nebius.cloud/ai/v1/jobs`; retain the
response immediately. Do not put bearer tokens in command arguments or logs.
See [official request fields](https://docs.nebius.com/serverless/jobs/manage).
The supplied `job-request.fixture.json` demonstrates rendering with fictitious
IDs and cannot be used as an approved job request.

One cpu-d3 / 4vcpu-16gb job, 100 GiB disk, 64 MiB shm, no GPU/SSH/public endpoint,
no mounted working database. Provider timeout is its minimum 3600 seconds; the
entrypoint has 300-second TERM / 10-second KILL bounds. Inside it the common
worker enforces CPU/RAM/wall/artifact requirements. The recipe cancels unfinished
runs and waits only for processes it created. A replaced VM does not have durable
workspace recovery; do not enable retries or claim cross-host recovery.

Record Job ID and query `nebius ai job get JOB_ID`; read
`nebius ai job logs JOB_ID` with a bounded observer, cancel after 5 minutes if not
terminal, and verify underlying VM/disk deletion. Do not blindly repeat create
after a transport timeout: list by the fixed name and reconcile the first POST.

## Read the actual result

Preserve raw logs. Extract the `WORLDFUZZ_RESULT` JSON line and verify the
`WORLDFUZZ_RESULT_SHA256` against that JSON plus its trailing newline. Retain its
WorldFuzz run ID, separate task/verification outcome, input/resource versions,
attempt history, usage and versioned JSON artifact representations. Obtain raw
JSON artifacts from the result before termination if exact original bytes are
needed; their hashes are distinct from versioned transport hashes.

The receipt intentionally says `cloud_job_identity_verified=false`: local
entrypoint execution cannot authenticate a Nebius job. Attach the independently
retrieved provider metadata, image digest and log hash before calling it cloud
evidence. A completed job without the required WorldFuzz result/checks is an
incomplete competition run. No physical replay or model benefit is implied.

Keep receipts privately until reviewed for publication. The static documentation
site and freely installable local quickstart are the judge access path; this job
does not create a public multi-user API.
