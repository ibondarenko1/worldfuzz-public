# Workspace usage and operational evidence

WorldFuzz uses the existing shared `get_service_metrics`, `get_run_audit` and
`get_workspace_usage` operations. They read local persisted records; there is
no separate analytics collector or automatic transmission. Request the
corresponding scoped permissions in an organization workspace.

| Field or operation | What it measures | What it does not measure |
| --- | --- | --- |
| metrics.requests | Persisted request counts grouped by operation and outcome | Unique users, unique human actions, sessions or adoption; internal compositions and retries also count |
| metrics.runs | Logical job counts grouped by current state | Robot success, physical safety or verified accuracy |
| metrics.retained_out_runs | Runs with completed retention records | All deleted bytes or time-series storage usage |
| metrics.attempts_with_unknown_tail | Attempts whose consumption has an unknown tail | An estimate of the missing CPU or a billing charge |
| get_run_audit | Run transitions, attempts, linked audit entries and retention state | A complete distributed trace or an independently authenticated history |
| get_workspace_usage | Quotas, reservations, measured phases, conservative holds and unknowns | Paid invoice, fleet usage or measured peak memory where it is unknown |

Counters are workspace scoped, cumulative over retained local audit rows and
not a fleet time series. A metrics request is recorded after its response is
computed, so the returned counters do not include that current call. Many
request audit entries have no run_id; get_run_audit returns linked entries plus
run events/attempts, not every surrounding discovery/read. Source calls made by
the trusted adapter also go through the dispatcher and can increase counts.

## Export through an existing client

With the normal HTTP SDK and a token supplied by the operator:

```python
import json
from pathlib import Path
from worldfuzz.platform.sdk import Client

# URL and token come from your approved client configuration; do not log tokens.
client = Client(url, token)
metrics = client.call("get_service_metrics")
Path("metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
audit = client.call("get_run_audit", {"run_id": run_id})
usage = client.call("get_workspace_usage")
```

CLI equivalents are `platform call get_service_metrics` and
`platform call get_run_audit --json-file run-selector.json` using your existing
placement/authority configuration. MCP uses those bare tool names. The example
walkthrough saves metrics.json, audit.json and usage.json through the same SDK;
its actions.json preserves the surrounding requests separately. Do not replace
unknown costs with zero or convert reservations into measured consumption.

For reports, label actor and origin explicitly: developer integration, internal
agent study or independently observed external use. WP36 is developer-operated
numeric integration and counts as **zero observed external adopters**. That is
not a census asserting no one elsewhere uses the project. No internal test,
download or successful job alone establishes a distinct external user. Review
IDs, paths and source metadata before sharing exports; see SUPPORT.md.
