# One published navigation task

This is an authored invocation example, not a saved successful run. It uses the
bundled Moving AI case `movingai:0`, the `movingai.octile.v1` profile and
`solve_navigation`. It requires no model API, image/video, GPU or data download.

Resolve `movingai:0` with `find_resources`. Check the workspace and budget, then
pass this structure to the bare MCP tool `run_goal`, adjusting limits only to the
user's request and available capacity:

```json
{
  "goal": {
    "operation": "solve_navigation",
    "profile": "movingai.octile.v1",
    "parameters": {"case_indices": [0], "max_cases": 1, "algorithm": "astar"},
    "max_seconds": 30,
    "resource_limits": {"cpu_seconds": 20, "memory_bytes": 268435456, "artifact_bytes": 1048576},
    "verification": {"mode": "metric_recompute"}
  },
  "resource_id": "movingai:0",
  "idempotency_key": "my-first-navigation-0",
  "wait_seconds": 20,
  "include_artifacts": true
}
```

The token needs the selected discovery tools and the internal operations
`run_goal`, `compile_goal`, `prepare_plan`, `execute_plan`, `get_run`, `get_result`,
`get_result_report`, and `get_artifact_json` for the requested artifact view.
`run_goal` enforces these scopes; do not invoke service internals to bypass them.

If still waiting, retain that key and goal and use the returned run ID. A final
report should give the actual resource and index, run ID, execution state, checks
performed, task outcome and artifacts. Grid validity and recomputed path length
are offline evidence; even `no_violation_found` does not prove robot safety.
