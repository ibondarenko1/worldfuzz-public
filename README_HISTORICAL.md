> Historical snapshot before WP34. Version, roadmap and experiment claims below describe their original checkpoints. Use [the current README](README.md) for the platform product.

# WorldFuzz

## Local task platform (0.4.0rc1)

Run `python -m worldfuzz platform serve` for the shared robotics task workspace.
HTTP, Python SDK, JSON CLI and MCP stdio expose the same 13 operations with
versioned plan contracts, shared local authorization and validated responses.
Published navigation, checked episode exports and paired trajectory metrics run locally.
See [integration guide and exact supported scope](docs/PLATFORM.md).


Start with existing published tasks and independently supplied answers.

The default dashboard page now shows nine completed Moving AI city-map tasks from Berlin, Boston and Denver, with original maps, recorded routes and published versus measured path lengths. The inputs were downloaded unchanged from the official dataset. All nine SciPy Dijkstra routes passed the independent checker; no failure was manufactured. [Recorded results and method](examples/published-street-cases-20260910/README.md).

```bash
python -m worldfuzz dashboard --open
```

The bundled report is an explicitly labelled recording and needs no numerical packages to view. To execute the same fixed cases yourself:

```bash
python -m pip install -e ".[benchmarks]"
python scripts/run-published-benchmarks.py --out artifacts/new-published-run
```

This makes at most 12 local solver calls, with networking and provider credential access disabled, and saves new evidence plus an interactive standalone report. The benchmark uses point-agent grid pathfinding, original diagonal rules and grid-unit lengths. It is separate from the DWA simulation below and is not a physical-robot test. Data: Moving AI Lab, Nathan Sturtevant; city maps provided by Konstantin Yakovlev and Anton Andreychuk, Open Data Commons Attribution License. Full notices accompany the inputs.

## Earlier authored navigation examples


Choose a robot task, test navigation in simulation and keep a repeatable result.

WorldFuzz runs entirely in software. Choose **Narrow passage**, **Obstacle detour** or **Timed delivery**, preview the map, and adjust robot size, required clearance, mission deadline and two navigation settings through ordinary fields. JSON is optional advanced input. Hardware, a customer prototype and outside specialists are not required.

```bash
python -m pip install -e ".[navigation]"
python -m worldfuzz dashboard --open
```

The earlier guided task workspace remains at `/regression`. Each built-in task starts with a two-simulation comparison. You can increase the search/reduction budget, inspect recorded paths, save evidence and reuse a failing case after changing a setting. The [navigation guide](docs/NAVIGATION_REGRESSIONS.md) explains limits, exports and CLI commands.

The [three-task acceptance](examples/self-contained-tasks-20260910/README.md) used 10 local simulations and no provider calls. It retains both regressions and the obstacle-detour result in which neither configuration completed. The initial [external-controller checkpoint](examples/external-navigation-20260910/README.md) remains available. The current integration uses pinned PythonRobotics DWA in a bounded 2D simulation. Additional robot stacks and AI-assisted task creation are later extensions.

Version 0.2.0 adds Windows credential setup, a Codex launcher, per-runtime diagnostics and an encrypted connection handoff to the executable evaluation engine. On September 9, 2026, one live Nebius preflight passed with `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`: 36 prompt tokens, 82 completion tokens, and validated strict JSON. See the [recorded response metadata](examples/windows-setup-20260909/preflight.json). A separate Work request then generated one validated scenario: 583 prompt + 842 completion = 1,425 tokens. Its local execution, reduction and replay passed. See [first live evidence](examples/first-live-20260909/README.md). Comparative search advantage and Nebius cloud simulation remain unverified.

## Open the local dashboard

From this checkout, with Python 3.11 or later:

```bash
python -m worldfuzz dashboard --open
```

Choose a fixed task, a policy, random or crossing-time search, and a scenario limit. The dashboard runs both policies locally, animates their recorded traces on one timeline, separates closest approach from route completion, and downloads verifiable evidence. You can cancel a search and verify its partial journal. No API key, frontend installation or paid service is needed. The saved Nemotron case and tuning-pilot recordings are explicitly labeled.

The [dashboard guide](docs/DASHBOARD.md) describes limits and replay commands. Its [recorded HTTP acceptance run](examples/dashboard-20260910/README.md) completed 12 scenario pairs and independently reran all 24 simulations. [Chrome visual and interaction QA](examples/browser-qa-20260910/README.md) now covers playback, search, cancellation and verification. Windows and browser download completion remain unverified.

## Current state

Stage 1 and the campaign-evidence engineering gate are complete. New searches retain attempts, failures, cancellation and known/unknown usage, with verifiable no-finding results. See [campaign evidence](docs/CAMPAIGN_EVIDENCE.md) and the [offline checkpoint](examples/campaign-evidence-20260910/README.md). The [offline pilot](examples/offline-pilot-20260910/README.md) is now complete: crossing-time search found a violation on its first attempt in both tasks; hardened avoided distance violations but finished only 165/192 missions. The local interface for search, replay, mission outcomes and evidence download is implemented; its browser/Windows validation and later model-assisted requirement workflow are the next product gates. The earlier [model-comparison draft](docs/BENCHMARK_PROTOCOL_DRAFT.md) remains unexecuted; these tasks do not justify a paid time-to-first-finding comparison. Follow [START_HERE_RU.md](START_HERE_RU.md) only when setting up or reprovisioning an environment. Use [AGENTS.md](AGENTS.md), [STATUS.md](docs/STATUS.md) and [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) to continue from the current gate. Windows DPAPI access and inference are verified. The shared repository is private at [ibondarenko1/worldfuzz](https://github.com/ibondarenko1/worldfuzz). Builder registration and credit activations are user-reported complete; credited balance remains unverified. Work credential import, catalog access and its first scenario-generation request are verified. Do not repeat completed setup calls. Public connection requests and encrypted envelopes are intentionally excluded from Git; a fresh receiver needs a new request from its intended Work runtime because the original request was consumed.

## Command-line workflows

Python 3.11 or later. Run commands from this extracted directory. No installation, API key, physical robot, GPU or network connection is needed for simulation and verification.

```bash
python -m worldfuzz demo --out artifacts/demo
python -m worldfuzz verify artifacts/demo
python -m worldfuzz search --strategy random --seed 42 --budget 24 --out artifacts/random
python -m worldfuzz verify artifacts/random
python -m unittest discover -s tests -v
```

Use a new output directory for each run. Existing evidence is preserved. The documented demo is a regression fixture. The random command performs actual bounded search, with every executed scenario recorded.

To replay the saved scenario directly:

```bash
python -m worldfuzz replay artifacts/demo/scenario.json --policy hardened --out artifacts/hardened-replay.json
```

Optional visualization requires Pillow. Install the `render` extra or Pillow in your own environment, then:

```bash
python -m worldfuzz render artifacts/demo --out artifacts/demo/replay.gif
```

## What this release implements

- Deterministic robot and pedestrian motion, delayed/missing observations, conflicting sensor signals and synthetic command categories.
- Independent ground-truth monitors, including closest approach between simulation samples.
- Random search and a validated, bounded Nemotron planner adapter.
- Fixed-point removal of unnecessary scenario factors, three identical replays, hardened-policy comparison and two nominal mission controls.
- Evidence capsules with SHA-256 hashes, exact traces, source fingerprints, simulation replay and independently recomputed outcomes.
- Side-by-side GIF and PNG replay with distances and mission results taken from saved traces.
- Windows DPAPI key storage, Codex process launcher, secret-free diagnostic reports and explicit one-use encrypted transfer.
- Matched-speed nominal controls and explicit identification of missions impossible at the requested speed.
- Durable campaign journals, independent model-attempt limits, cumulative known/unknown token accounting, and schema-v3 verification of no-finding, failed and incomplete campaigns.
- Fixed tuning tasks, a disclosed distance-only policy ablation, equal-budget random/crossing-time search, and independently replayable offline pilot evidence.
- A loopback-only English dashboard, synchronized trace playback, distance chart, bounded paired searches, cancellation, verification and allowlisted evidence downloads. Interactive searches use a separate versioned contract and preserve the frozen pilot.

The human separation requirement stays fixed at 0.75 m. Generated scenarios cannot lower it or remove the pedestrian. The planner can return only schema-validated parameters. It cannot execute code, supply a policy implementation or control devices.

## Evidence and verdicts

`FIX_CONFIRMED_FOR_THIS_SCENARIO` requires the baseline violation to repeat, the hardened policy to have zero monitored violations and complete the challenge mission, and both nominal controls to finish without violations.

`SAFETY_BLOCKED_MISSION_INCOMPLETE` means the hardened policy avoids the monitored violation but misses the challenge deadline. `MISSION_INFEASIBLE_AT_REQUESTED_SPEED` identifies settings whose nominal travel time already exceeds the horizon. `COMPARISON_INCOMPLETE` records other unmet checks. `NO_COUNTEREXAMPLE_FOUND` says only that every allowed search rollout completed without the target violation. It now produces verifiable evidence. `FAILED`, `CANCELLED` and `MODEL_BUDGET_EXHAUSTED` preserve prior work; a leftover journal without a finished capsule verifies as `INCOMPLETE`. See [outcome and accounting semantics](docs/CAMPAIGN_EVIDENCE.md).

The CLI campaign's 24-run cap applies to search. Reduction, replay and control runs are separately counted and included in the total. Dashboard paired searches accept 1–48 scenarios, at two local simulations per scenario, without reduction or nominal controls. They report outcomes without issuing a complete-fix verdict. Verification re-executes recorded work and is counted separately.

Reduction establishes 1-minimality under five listed factor resets. It does not establish a global mathematical minimum. Timing, geometry seed and speeds remain fixed. A reduced case may need none of the original fault factors, which is recorded without inventing a compound cause.

The capsule verifier checks local reproducibility and content consistency. It does not authenticate historical cloud service execution. SHA-256 hashes are not signatures.

## NVIDIA and Nebius

The [Builder Program](https://dev.nebius.com/builders) registration and activations are recorded in the current plan. Both current environments are connected. [NEBIUS_SETUP.md](docs/NEBIUS_SETUP.md) and the commands below are reference instructions for a new environment or a separately budgeted future run.

```bash
python -m worldfuzz preflight
python -m worldfuzz models
python -m worldfuzz preflight --live
python -m worldfuzz search --strategy nemotron --seed 42 --budget 1 --out artifacts/nemotron-first-scenario
```

The final two commands make paid-capable inference requests. Their setup and first-scenario limits have already been consumed; do not repeat them as part of the next engineering gate. Any further live run needs its own recorded scope and limits. There are no automatic paid retries or silent substitutes for model responses. Each new campaign records the selected model and request limits before the first call. Valid responses record model, usage, request identifiers and hashes; rejected responses retain reported usage and uncertain calls retain an explicit unknown count. An explicit configuration failure never becomes a mock success.

Nemotron receives the public environment contract and numeric observations from previous attempts. It does not receive policy source or private benchmark solutions. `compile_claims` is an adapter method for four fixed monitor templates; the CLI currently evaluates the fixed human-distance objective. Arbitrary natural-language rule interpretation remains future work. The dashboard currently offers local search and playback of recorded model evidence, with no live inference route.

Cloud simulation jobs, hosted judge access, Cosmos critic and equal-budget model benchmarks are not implemented in this release. The local HTTP API is implemented. See [HACKATHON_PLAN.md](docs/HACKATHON_PLAN.md) for gates and verified submission requirements.

## Simulation limits

This fixture uses point-center distances, instantaneous stopping and a known straight crossing direction. It does not model robot body clearance, braking dynamics, perception noise, contact physics or a real warehouse. The hardened crossing guard relies on the stated pedestrian-direction assumption. These experiments support claims about this software fixture only.

The archived v0.1 random-search example preserves an operational failure that misses the fixed 20-second mission deadline. New v2 capsules use matched-speed nominal controls and report mission feasibility explicitly. Archived v1 source fingerprints will differ from this release; their traces are retained as historical evidence, not current model-run results.

License: MIT.

### Reusable published robotics data

Open the local data library with `python -m worldfuzz dashboard --open`. Import the included Moving AI results from the page, filter verification levels and export complete episodes. The optional Can Paired adapter adds published manipulation observations/actions with source labels. [Setup, data semantics and export commands](docs/DATA_LIBRARY.md) · [209-episode acceptance recording](examples/data-corpus-20260910/README.md).
