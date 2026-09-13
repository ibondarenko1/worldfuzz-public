# Trajectory extent: install and run without a checkout

This independently packaged, WorldFuzz-authored example summarizes two numeric
XYZ rows. It is **not a published robotics result, independent accuracy check,
controller replay or external adoption**. Adapter 0.2.0 declares
`worldfuzz>=0.4.0rc4,<0.5` and Python 3.11+. WP36 tests 0.4.0rc5 on its recorded
Windows/Python environment; the dependency range is not a tested version matrix.

Obtain the supplied `worldfuzz-adapter-kit-0.4.0rc5.zip` and compare its SHA-256
with the delivery receipt. Extract it outside the source repository. It contains
core/example wheels, example source sdist and developer guides. No PyPI or
catalog publication is claimed; do not substitute an unverified public package.

## Install in one isolated Python environment

Activate an environment with the base WorldFuzz dependencies installed, or let
pip resolve them while installing the supplied core wheel. No robot, GPU, HDF5,
rendering or inference extra is needed. `python` must identify that environment.
From the extracted kit directory:

```sh
python -m pip install ./worldfuzz-0.4.0rc5-py3-none-any.whl
python -m pip install --no-index --no-deps ./worldfuzz_example_trajectory_extent-0.2.0-py3-none-any.whl
python -m pip show worldfuzz worldfuzz-example-trajectory-extent
```

The second command uses a reviewed local wheel. `--no-deps` avoids changing the
installed core; first ensure its requirements are met. Installation does not
enable the adapter. Do not use an editable checkout for this integration check.
To develop it, unpack the included sdist, edit/build a new version, then install
and explicitly enable that exact version.

## Start the common service

Choose a new workspace, not your only working database. In PowerShell:

```powershell
$env:WORLDFUZZ_ADAPTERS = 'trajectory-extent==0.2.0'
python -m worldfuzz platform --root ./workspace/results --store ./workspace/catalog.sqlite3 serve --port 8766
```

On POSIX shells use `export WORLDFUZZ_ADAPTERS=trajectory-extent==0.2.0` before
the same command. The runner supports Windows and Linux; an unsupported OS or
missing mandatory resource mechanism must refuse execution. Choose another free
port if 8766 is occupied and use the printed URL. No Docker or WSL is needed.

## Execute the installed SDK example

In another terminal using the same installed Python:

```sh
python -m trajectory_extent.walkthrough --url http://127.0.0.1:8766 --out ./example-output --key extent-example-001
```

This uses the existing personal-workspace loopback bootstrap. For an organization
gateway, inject the operator-issued scoped token as `WORLDFUZZ_SESSION_TOKEN`;
never paste it into a command or report. The kit's ADAPTERS.md lists the scopes.

The command discovers the exact profile, registers its packaged numeric input,
uses common `run_goal`, reads the real result and checks the downloaded file
hash. The server launches the existing separate worker. Limits are 20 seconds
wall, 10 CPU seconds, 256 MiB committed memory on Windows (address space on
Linux), 1 MiB artifacts and a 20-second wait. Expected bounds are `[2,-3,1]`
to `[5,6,4]` metres, two samples, frame `authored_shared_xyz`.
`control_outcome_verified=false`; verification is integrity and
`proven_result=not_evaluated`.

Inspect `example-output/receipt.json`, `actions.json`, `run.json`, `extent.json`,
`report.json`, `audit.json`, `metrics.json` and `usage.json`. Missing adapters,
wrong versions, denied scopes or unfinished jobs fail the walkthrough; no skip
is counted as acceptance. If waiting expires, preserve `request.json`, its key
and run ID. Read `get_run`/`get_result` or resend that exact request using the
normal SDK/CLI. Keep the original output directory and do not create a second job.

After reading the terminal result, stop the server with Ctrl+C. Finish or cancel
active work before stopping. A restart reconciles uncertain attempts; it must
not silently repeat them. Preserve the new workspace and outputs for inspection.
Do not stop unrelated Python, Node or database processes.

The kit includes ADAPTERS.md, CONTRIBUTING.md, SUPPORT.md, COMPATIBILITY.md and
USAGE_METRICS.md. This README is also included in the example's package metadata.
Original rc1–rc4 evidence remains historical; this walkthrough does not repeat
the WP33 matrix or the browser/agent studies.
