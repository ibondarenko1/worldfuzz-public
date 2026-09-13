# Proposed single-run allowance (not yet authorized)

September 13, 2026. Currency USD, excluding unknown account taxes. Proposed total
owner ceiling **$1.00**, not an assertion of a provider-enforced dollar cap. Do not
start until the project/rates/credits and shutdown conditions below are confirmed.
If they do not fit $1, stop and return a revised estimate before any paid action.

| Component | Fixed request / reserve | Observed or unknown |
| --- | --- | --- |
| NVIDIA model | `nvidia/Nemotron-3_5-Lightning`, one request, no retry, output cap 512 tokens (including provider-accounted reasoning), transport timeout 30 s | Exact ID present in authenticated GET /models. Catalog modal: 1,024K context; approximate $0.06 input / $0.24 output per million, Global/Base, OpenMDW v1.1. Detailed account SKU rates still need confirmation |
| Input | Frozen public resource facts + fixed prompt/schema, maximum 8,192 serialized bytes | Byte limit enforced locally; exact provider prompt token count remains unknown before execution. Reserve the entire displayed context: 1,048,576 input tokens, not an invented character/token conversion |
| Inference reserve | `1,048,576 × .06 / 1e6 + 512 × .24 / 1e6 = $0.06303744` | Conservative pricing assumption; provider response usage retained even if rejected or incomplete. No network retry after an uncertain request |
| Job | `cpu-d3`, `4vcpu-16gb`, zero GPU, one job, regular VM, no schedule/endpoint | Proposed region eu-north1; account preset/capacity/subnet not yet checked |
| Job time | inner process 300 s plus 10 s kill grace; one task 30 s wall / 20 CPU seconds / 256 MiB / 1 MiB artifacts | Nebius documented minimum job timeout is **3,600 s**. Price reserve is the whole hour, not 300 seconds. Provisioning/teardown accounting must be checked |
| Compute reserve | `4 × .012 + 16 × .0032 = $0.0992` for one hour | Public AMD Genoa rate; account-specific quote unverified |
| Disk | 100 GiB Network SSD, no external data volume; reserve two hours from creation to deletion | `100 × .071 × 2 / 730 = $0.019452055`; job disk removed on completion/cancellation according to documentation |
| Result transport | one JSON log result ≤1 MiB plus SHA-256; retrieve once, locally retained with job metadata | No persistent bucket or public endpoint planned. Log retention/ingest, image storage/transfer and taxes need an account quote; **not zero by assumption** |
| Calculated subtotal | **$0.181689495** | Known-rate conservative calculation, not billed cost |
| Remaining allowance | **$0.818310505** for confirmed image/log/network/tax/teardown charges | A reserve, not a measured fee. No job allowed while maximum total cannot be bounded to $1 |

Sources: [model catalog](https://tokenfactory.nebius.com/?modals=endpoint-details&model-id=nvidia/Nemotron-3_5-Lightning),
[account prices](https://tokenfactory.nebius.com/organization/prices),
[Compute prices](https://docs.nebius.com/compute/resources/pricing),
[Jobs management](https://docs.nebius.com/serverless/jobs/manage),
[Serverless terms](https://docs.nebius.com/legal/specific-terms/serverless-ai).
Catalog UI prices are explicitly approximate. Do not replace a rate unknown with
the approximate value when authorizing spend. No billing API response was obtained.

## Account checks

Cloud project, permission, quota, exact prices and applicable balance must be checked privately before approval. No account or balance observations are distributed.

## Stop and reconcile

1. Freeze the request hash, rc6 wheel hash, base/container image digest, quote and
   explicit approval. Copy authorization.example.json outside the checkout; the
   prepared copy remains `approved=false`. One inference attempt is durably
   recorded before transport. Timeout/unknown usage means stop, not repeat.
2. Submit one rendered job request only after separate approval. Record the
   returned job/operation ID immediately. An uncertain POST is not retried: list
   jobs by the fixed name and reconcile first. Do not schedule or auto-recreate.
3. Poll/read logs for that exact Job ID. At 5 minutes request cancellation with
   `nebius ai job cancel JOB_ID`; verify terminal state and deletion of the
   underlying VM/disk. The provider 1-hour timeout is the fallback if the local
   observer disappears, not a promised immediate cost stop.
4. The recipe cancels its own unfinished task and waits for only workers it
   started. The container command has a bounded TERM/KILL timeout. It does not
   affect other processes or cloud resources. Stop on errors or incomplete output.
5. Retrieve the result marker and hash, retain original bytes with provider job
   metadata/image hash and WorldFuzz run/artifact verification. Logs alone do not
   authenticate cloud execution. Confirm billed usage afterward; unknown remains
   unknown. Remove only the newly approved image after retrieval if its retention
   costs would exceed the allowance. Preserve local evidence for judges.

Automatic recovery across replaced VMs, remote durable SQLite, log truncation,
maintenance restart policy and provider timeout/teardown behavior have not been
validated. Confirm a no-retry job policy before creation; if the account cannot
prevent an automatic second execution within this allowance, do not submit.
This prepared one-shot job does not establish a distributed runner service.
