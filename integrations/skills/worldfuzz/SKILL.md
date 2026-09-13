---
name: worldfuzz
description: Run supported robotics tasks and inspect or export their checked results through an existing WorldFuzz MCP connection. Use for WorldFuzz navigation, recorded robot or sensor data, comparison and dataset requests.
license: MIT
metadata:
  version: "0.4.0rc6"
  mcp-name: "io.github.ibondarenko1/worldfuzz"
---

# WorldFuzz

Use the connected WorldFuzz service to complete the user's bounded task and
report the actual run, task outcome and verification scope separately.
Requires WorldFuzz 0.4.0rc6, an authorized workspace placement and an MCP-capable
agent client. Optional task runtimes must already be installed.

## Choose and execute

- Resolve the user's data with `find_resources` and retain the exact canonical
  resource ID. Moving AI indices start at zero: `movingai:8` selects case 8.
  Do not infer an unavailable resource or silently select a neighboring case.
- Use `get_capabilities` and, when needed, `describe_operation` for the current
  service schema. Adapter operations such as `solve_navigation` belong inside
  `goal.operation`; they are not MCP tool names. Let the client add its server
  prefix once. Never send a composed server/tool name back as the bare tool name.
- Check `get_workspace_usage` and `get_budget` where available. Preserve the
  user's wall, CPU, memory and artifact limits. Workspace membership does not
  grant source rights. A missing scope or compatibility requirement requires a
  concrete correction, not a broader token, a substitute task or repeated discovery.
- Use `run_goal` for one typed operation. It compiles, prepares, authorizes,
  executes and reads through the common dispatcher. For ambiguous goals, report
  the returned missing fields. A campaign request needs the campaign operations;
  several standalone jobs do not complete a campaign.
- Keep the goal and idempotency key unchanged after a waiting timeout; retain
  the run ID and read or resume the same logical run. Do not create a replacement
  job. A timeout is not a failed robotics task.

## Read the evidence

- `prepare_plan`, `verify_result` and `export_result` return preparation. No run
  or export artifact has been created merely because preparation succeeded.
- Confirm completion from the run and `get_result` / `get_result_report`, including
  actual canonical resource, performed checks, proven result and limitations.
  A client exit code or fluent final response is not execution evidence.
- Confirm export only after completed execution and existing files. Check the
  returned hashes on original downloaded bytes. `get_artifact_json` is an exact
  versioned JSON view, not the original byte stream; its original hash names the
  source artifact. Exact nanoseconds stay decimal strings.
- Recorded-state viewing and integrity are not action replay. Simulator or
  offline metric checks do not prove physical reproduction. Unknown conversions,
  frames or controller parameters remain unknown. Held-out data is not training
  data. Report client/model-filter stops separately from service refusals.

For a first bounded task, read [the navigation example](references/navigation.md).
For other tasks, use the installed WP34 documentation at the operator's configured
base URL: [tasks](http://127.0.0.1:8766/docs/tasks.html),
[clients](http://127.0.0.1:8766/docs/connect.html),
[comparison](http://127.0.0.1:8766/docs/compare.html),
[export](http://127.0.0.1:8766/docs/export.html), and
[errors](http://127.0.0.1:8766/docs/errors.html).
These loopback links work only while the local docs service is running. Tool
discovery supplies live contracts without starting another server. Documentation
can also be exported with `worldfuzz platform docs`; no public site is assumed.
