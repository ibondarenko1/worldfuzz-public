# WorldFuzz

<!-- mcp-name: io.github.ibondarenko1/worldfuzz -->

Run a bounded robotics task, keep its evidence, and reuse checked results or data.
WorldFuzz connects a local workspace to the same **platform service** through a
browser, Python SDK, CLI and MCP. A run keeps exact inputs and versions, rights,
resource limits, task outcomes, verification and downloadable artifacts together.

Current free preview candidate: **0.4.0rc6**, Python **3.11+**. Publication is pending.
No public site, PyPI release or MCP registry listing is claimed.
WP33 remains **ACCEPTED_TESTED_SCOPE** for the retained rc1 native and rc2 Kali
container receipts; later candidates do not repeat that matrix. See the
[release scope](docs/RELEASE_SCOPE.md) for changes and retained evidence limits.

## Start with the platform

Obtain the operator-supplied release bundle and check its SHA256SUMS. In a new
Python environment, install the supplied wheel. The package resolves its pinned
base dependencies; installation needs network access unless they are cached:

```sh
python -m pip install ./worldfuzz-0.4.0rc6-py3-none-any.whl
python -m worldfuzz platform --root workspace/results --store workspace/catalog.sqlite3 serve
```

Use a new `workspace/` directory. Open **http://127.0.0.1:8766/** for the UI and
**http://127.0.0.1:8766/docs/** for the English documentation. No frontend build,
model key, GPU, device or HDF5 download is needed for published grid navigation.
The server stays on loopback. Organization workspaces require a scoped login.

For one new task, open a second terminal in the same directory/environment:

```sh
python -m worldfuzz platform --root workspace/results --store workspace/catalog.sqlite3 run navigation --resource movingai:0 --case-index 0 --key quickstart-nav-0 --max-seconds 30 --cpu-seconds 20 --memory-mib 256 --artifact-mib 1 --wait-seconds 30 --export workspace/export-nav-0
```

This uses `run_goal` and the shared runner. It checks a route against original
Moving AI inputs and the independent route checker, then returns a run ID and
separate run/task/verification outcomes. The new export directory contains the
summary, JUnit, result and versioned JSON artifact views. Original artifact bytes
remain available from the authenticated service. Reuse the same goal/key after
a wait timeout; do not create a second job or overwrite a completed export.
Stop the server you started with Ctrl+C when finished.

## Choose a supported process

| Process | Input | Result and its limits |
| --- | --- | --- |
| Published navigation | Included map/scenario, exact zero-based `movingai:N` | New A*/Dijkstra route, independently checked grid validity and published length |
| Recorded Can data | Existing catalog episodes and linked groups | Integrity, recorded-state windows or checked export; reading states is not replay |
| Can action replay | Existing episode, exact XML/controller supplement, pinned optional runtime | Headless action execution and independent object/bin/gripper geometry; not physical reproduction |
| Trajectory evaluation | Registered prediction/reference XYZ tracks, matching times and declared frame/metres | Unaligned position RMSE; MCAP and unknown conversions are refused |
| ROS/MCAP inspection | Registered recording and declared time/frame/unit metadata | Message inventory and exact nanoseconds; no invented commands or flight outcome |
| External engine | Included published Pymunk task and pinned engine | Actual simulation with independently checked trace; no physical equivalence |
| Dataset generation | Complete authorized groups and explicit purpose | Versioned JSONL/LeRobot v3 manifest, lineage and train-only normalization; no training |

[Integration and workspace guide](docs/PLATFORM.md) explains client choices.
[Connect an MCP client and install the optional Agent Skill](integrations/mcp/README.md)
for one bounded task with the existing Python stdio server.
The served **Supported tasks** pages give each exact adapter operation, profile,
requirements, bounded example and evidence scope. **API** and **Profile reference**
are generated from the existing contracts, not a second manual schema copy.

## Read, compare and export

The UI separates a new run from a saved recording. `compare_results` compares
compatible completed results; it does not run another task. `export_result`
prepares a plan only. Export is confirmed by completed execution and existing
hash-checked files. Source rights remain separate from workspace membership;
linked pairs and held-out splits remain protected.

Develop an installed extension with the [adapter guide](docs/ADAPTERS.md) and
[contribution process](CONTRIBUTING.md). The separately supplied developer kit
works without a source checkout. [Compatibility](docs/COMPATIBILITY.md),
[support](docs/SUPPORT.md) and [local usage metrics](docs/USAGE_METRICS.md) state
the current limits; no external-adoption or telemetry claim is implied.

See [historical WP33 installation and recovery evidence](docs/RELEASE_INSTALLATION.md),
[organization access](docs/ORGANIZATIONS.md), [workspace recovery](docs/WORKSPACE_RECOVERY.md)
and [documentation export instructions](docs/DOCUMENTATION.md). Documentation is
ordinary HTML without mandatory JavaScript and can be exported with a configured
base URL. Preparing a sitemap or llms.txt is not publication or indexing.

## Evidence and status

M7 is **ACCEPTED_LIMITED_SCOPE**. The twofold action-reduction target was not
achieved; both independent agent studies retain their original failed verdicts.
Software integrity, offline metrics and simulator replay do not prove physical
robot behavior or universal safety.

- [Release scope, historical acceptance and limits](docs/RELEASE_SCOPE.md)
- [Historical dashboard and experiment commands](README_HISTORICAL.md)
- [Saved published navigation report](examples/published-street-cases-20260910/README.md)

Historical reports are recordings, not fresh execution. The old `dashboard`
command remains available through the historical guide; it is separate from the
current platform workflow. [MIT license](LICENSE); third-party source notices
remain with their inputs and the delivered package.
