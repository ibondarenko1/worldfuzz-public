# Develop a trusted installed adapter

WorldFuzz 0.4.0rc5 loads explicitly enabled Python distributions through the
existing `worldfuzz.adapters` entry-point registry. An adapter adds a profile
and operation without editing service, worker, HTTP, SDK, CLI or MCP modules.
**Third-party adapters are trusted installed code.** They have the operator's
filesystem and network access. Registry and runner are not a sandbox for
untrusted Python.

Start with the separately supplied `worldfuzz-adapter-kit-0.4.0rc5.zip`. Its
README covers installation of local core/example wheels, explicit activation,
starting the common service, and the installed SDK walkthrough. It requires no
checkout. The example uses authored numerical data, not a published robotics
result or independent adoption. Installation alone does not enable an adapter.

## Identity and methods

```toml
[project.entry-points."worldfuzz.adapters"]
trajectory-extent = "trajectory_extent:ExtentAdapter"
```

The entry point is instantiated without arguments. Set
`WORLDFUZZ_ADAPTERS=trajectory-extent==0.2.0` in the service environment.
PowerShell: `$env:WORLDFUZZ_ADAPTERS = 'trajectory-extent==0.2.0'`.
Comma-separated pins are supported. This is the entry-point name and exact
distribution version, not the distribution name `worldfuzz-example-trajectory-extent`
or module `trajectory_extent`. Missing, ambiguous, duplicate or mismatched
entries fail startup. Requests cannot install packages or choose arbitrary imports.

| Method | Responsibility |
| --- | --- |
| `describe()` | Finite JSON profile, operation schemas, source policies, versions and limitations |
| `resources(service)` | Discover supported workspace resources without execution; an empty list is valid for caller-registered inputs |
| `snapshot(service, request)` | Check actual input type and semantics, then freeze IDs, hashes and versions |
| `execute(service, plan, records, artifacts, check, artifact)` | Recheck sources, checkpoint work and collect records/artifacts with supplied callbacks |

For nested reads use `service.invoke('get_asset', asset_id=...)`. It crosses the
same scoped dispatcher and output validation as clients. Direct
`service.get_asset(...)` or private table access can bypass the dispatcher and
is not the authoring pattern. The example needs `get_asset` during preparation
and execution and rejects MCAP before a job or reservation exists.
Declaring a source kind does not convert data.

## Mandatory source contract

The installed four-method contract remains `contract_version: "1.0.0"`.
Required descriptor fields are `adapter_id`, `version`, `contract_version`,
`profile`, `license`, `execution_mode`, **`source_contract`**,
`environment_lock`, `limitations` and `request_schemas`.
The profile declares ID, domain, state, action (null when absent), observation,
outcome and unique operations. Every operation requires a closed object schema.

The runtime schema subset supports object, array, string, boolean, integer,
finite number, properties, required fields, enums, descriptions and numeric/size
bounds. Unknown keywords are rejected. It is not full JSON Schema. Discover
the authoritative definitions through `get_capabilities` and `describe_operation`;
`worldfuzz.platform.adapters.check_descriptor` validates descriptors. This guide
does not maintain a second schema.

Source kinds are `m1`, `registered_trajectory_v1`, `registered_sensor_v1`,
`external_engine_v1` and `can_replay_v1`. These select existing resolvers.
An M1 source requires exact domain, `asset_ref` and `profile_ref` objects
containing ID and version. Current M1 resolvers cover the packaged domains.
Other kinds contain only `kind` and `operations`. Arbitrary new source resolvers
are not an installed extension API.

Every declared operation requires one exact source policy:

| Field | Meaning |
| --- | --- |
| `required_rights` | All four subjects: data, code, model, scene. Each maps to inspect/evaluate/train/export/redistribute, or null when not used |
| `profile_fields` | Required semantics from components, state_schema, action_schema, observation_schema, frames, units, time_model |
| `require_known_profile` | Whether unknown profile facts block the operation; false preserves unknowns |
| `accepted_rights_statuses` | Explicit subset of declared/verified; accepting a declaration does not independently verify the license |
| `execution_mode` | metadata_inspection, offline_evaluation, observation_replay, closed_loop_simulation or external_result_ingestion |

The example requires data:inspect and code/model/scene:null; accepts declared or
verified rights; requires components, observation_schema, frames and units; and
uses metadata_inspection. It reads prediction coordinates only. A paired
reference exists because the supported registration contract requires both.
Tracks have matching increasing seconds, XYZ metres and the caller-declared
shared frame. No calibration, transform, alignment, commands, accuracy or flight
outcome is inferred. Export and training require their own explicit policies;
registration grants neither automatically.

Token scopes and workspace membership are separate from source rights. The
common service checks CompatibilityDecision and PolicyAuthority at preparation
and again before execution. Revocation, expiry, changed inputs/environment and
missing rights block stale plans. An organization token for the full walkthrough
needs get_capabilities, find_resources, register_asset, get_asset, run_goal,
compile_goal, prepare_plan, execute_plan, get_run, get_result, get_result_report,
get_artifact_json, get_service_metrics, get_run_audit and get_workspace_usage.
Do not grant administration to make the example pass.

## Shared execution

Adapter operations such as `summarize_trajectory` belong in `goal.operation`,
not in an MCP tool name. Use the registered asset ID:

```json
{
  "goal": {
    "profile": "sensor.xyz.extent.v1",
    "operation": "summarize_trajectory",
    "parameters": {"asset_id": "REPLACE_WITH_REGISTERED_ASSET_ID"},
    "max_seconds": 20,
    "resource_limits": {"cpu_seconds": 10, "memory_bytes": 268435456, "artifact_bytes": 1048576},
    "verification": {"mode": "integrity"}
  },
  "idempotency_key": "extent-example-001",
  "wait_seconds": 20,
  "include_artifacts": true
}
```

Call `run_goal` through HTTP/SDK/CLI/MCP. It composes existing dispatched
compile/prepare/execute/read/report operations. After waiting expires, retain
the exact request and key, then read the existing run or repeat that request.
Preparation is not execution. Custom adapters use `platform call run_goal` or
the SDK; the scalar `platform run` workflows cover bundled use cases.

## Actual runner and evidence limits

The parent probes OS support before accepting a plan. Windows uses a
one-process Job Object for CPU time and committed memory. Linux uses RLIMIT_CPU
(rounded up to seconds) and RLIMIT_AS (virtual address space). These are different
memory semantics, not interchangeable RSS claims. The worker applies limits
before execute, uses an independent wall/CPU watchdog, and the parent reaps
overdue workers. Artifact callbacks enforce the aggregate byte limit. Required
unsupported capabilities, including network/filesystem isolation or devices,
block preparation. Third-party subprocess trees are not a supported extension
of the one-worker contract. Describe, snapshot and discovery run in the trusted
service; do not put heavy or unbounded work there.

`environment_lock` is a declaration; the runner pins actual Python identity,
installed package inventory, adapter bytes and input versions. The registry
neither resolves dependencies nor enforces arbitrary declared lock text.
Use installation metadata and operator checks. Restart after package changes:
changed fingerprints cannot execute old plans. The service admits one active
job, uses leases/heartbeats, preserves attempts and fences a worker that loses
its lease. Call `check()` before work and inside loops. Unknown consumption
stays unknown and can retain a conservative reservation.

Append JSON records and use `artifact(name, bytes)` instead of writing result
paths yourself. Common integrity checks cover collected bytes and manifests.
The example writes `extent.json`, but its calculations remain adapter-reported:
`proven_result=not_evaluated`. The walkthrough asserts expected values on two
rows as an integration check; it does not register an independent verifier.
Metric recomputation is supported only for the operations implemented in the
core verifier. Unsupported metric protocols and the generic replay verification
mode are refused. File integrity never proves simulator or physical replay.
A separate installed verifier-plugin API is not currently implemented.

## Conformance and maintenance

The kit walkthrough fails when its pinned adapter is absent. Absence is incomplete
acceptance, not a successful skip. Optional source-checkout tests are development
aids, not proof of installation. Test affected schemas, source kinds, snapshot
changes and scoped reads when extending the example.

See the kit's CONTRIBUTING, SUPPORT, COMPATIBILITY and USAGE_METRICS pages for
review, reproduction, version lifetimes and workspace counters. There is no
external-adoption claim, automatic telemetry or response-time SLA.
