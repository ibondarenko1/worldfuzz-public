# WorldFuzz platform integration

The current documentation build is 0.4.0rc3. The existing shared service exposes
42 operations through HTTP, Python SDK, CLI and MCP, plus the browser workspace.
The offline quickstart uses one bundled Moving AI task and no model credentials.
Use the [README](../README.md) to start a new workspace, then open `/docs/` on its
loopback server for the complete HTML guide.

## Guide map

| Page on the running service | What it explains |
| --- | --- |
| `/docs/quickstart.html` | Requirements, one new task, bounded waiting and exported evidence |
| `/docs/connect.html` | SDK, CLI, MCP and organization-scoped access |
| `/docs/tasks.html` | Exact input, operation, profile, requirements and verification for each process |
| `/docs/compare.html` | Compatible completed-result comparisons |
| `/docs/export.html` | Original artifacts, dataset manifests, groups, splits and source rights |
| `/docs/errors.html` | Concrete refusals, missing parameters and evidence limits |
| `/docs/api.html` and `/docs/openapi.json` | Generated operation/request/response reference |
| `/docs/profiles.html` | Generated bundled adapter descriptors and parameter contracts |

The same pages can be exported to ordinary static HTML using
[the existing documentation command](DOCUMENTATION.md). No publication or search
indexing is implied. The API remains version 1.0.0; product and API versions are
separate. Dynamic `/openapi.json` remains available at its established path.

## One service, explicit authority

Use identical absolute root/store paths for a local trusted-owner workspace:

```sh
python -m worldfuzz platform --root workspace/results --store workspace/catalog.sqlite3 serve
```

The catalog binds to its artifact root. Do not point another root at it or use
an organization-bound catalog as an unauthenticated local owner. With
`--authority`, local organization login replaces bootstrap and issues a token
scoped to one workspace and explicit operations. Every internal step of
`run_goal` passes the shared dispatcher. Membership, owner grants and source
rights remain separate, and revocation is checked again before worker execution.
See [organizations](ORGANIZATIONS.md), [source contracts](ENTITY_CONTRACTS.md)
and [workspace quotas](WORKSPACE_QUOTAS.md).

## Operation lifecycle

`run_goal` compiles a typed goal, prepares its immutable plan, executes with an
idempotency key, waits for a bounded interval and returns a common report. A
missing input is returned explicitly. The same key/goal keeps one logical job.
`waiting` keeps the run ID and does not mean robotics failure. The lower-level
`compile_goal`, `prepare_plan`, `execute_plan`, `get_run`, `get_result` and
`get_result_report` remain available without bypassing rights or response checks.

`prepare_plan`, `verify_result` and `export_result` are preparation only: no job
or export file is created by those responses. Read terminal execution and verify
existing artifacts before confirming an export. `compare_results` reads compatible
saved runs. A requested campaign uses `create_campaign`, `launch_campaign_case`,
`get_campaign` and `finalize_campaign`; standalone jobs do not complete it.

One logical job owns the local worker slot. Another request gets atomic
`RESOURCE_BUSY` before a new job or reservation. Windows Job Objects or Linux
CPU/address-space limits enforce supported resource ceilings; unsupported required
capabilities block preparation. Reconcile an interrupted job before explicit
resume. Events, attempts, partial artifacts, measured usage and unknown tails
remain distinguishable. See [operations and recovery](OPERATIONS.md).

## MCP names and evidence

The MCP server label is `worldfuzz`; `run_goal` is a tool name, and
`solve_navigation` is an adapter operation inside its goal. Do not add a second
server prefix. Stdio and the capabilities resource are supported; Streamable HTTP,
experimental Tasks and registry publication are not claimed. Agent/model filter
stops are client events, not WorldFuzz semantic refusals.

Use `find_resources` for exact IDs. `movingai:8` means zero-based case index 8.
The service cannot infer an original natural-language request it never received.
Nanosecond public values use decimal strings; original artifact hashes continue
to identify the stored bytes. Reading saved Can states is not action execution;
the separately pinned action-replay profile requires its exact XML/controller
supplement. Neither offline metrics nor simulator replay establishes physical
reproduction.

## Accepted placement scope

WP33 remains ACCEPTED_TESTED_SCOPE: retained rc1 Windows install/upgrade evidence
and actual rc2 rootful Kali/Linux Compose execution with persisted results.
The rc3 documentation changes do not repeat that matrix. Cross-host orchestration,
rootless/remapped Docker, external SSO and optional runtimes outside their receipts
remain unverified. M7 is ACCEPTED_LIMITED_SCOPE; the twofold action target and
independent-agent certification remain unproved.
