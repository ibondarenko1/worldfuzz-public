# Published city-map cases, September 10, 2026

These are existing tasks from the [Moving AI Lab street benchmark](https://www.movingai.com/benchmarks/street/index.html), not WorldFuzz-authored navigation examples. Original city/street grids and scenario rows were downloaded without modification. The source describes these maps as discretizations of building/road maps, supplied by Konstantin Yakovlev and Anton Andreychuk.

## Result

Nine of nine routes matched their published optimal lengths within the predeclared 0.00001 grid-unit tolerance. All returned routes independently passed endpoint, passable-cell, neighbouring-step and blocked-corner checks. The largest absolute length difference was approximately 4.8e-8, within the precision of the published values. A repeat of the first case returned the exact same path.

| Map | Original data rows, excluding header | Completed cases | Match |
|---|---|---:|---:|
| Berlin_0_256 | 233, 465, 697 | 3 | 3 |
| Boston_0_256 | 238, 475, 712 | 3 | 3 |
| Denver_0_256 | 235, 470, 705 | 3 | 3 |

Selection was fixed before execution: floor((N-1)×q)+1 for q=0.25, 0.5 and 0.75 in each source file. The complete original rows are in `selection.json`, with file line numbers. No solver output was used to select or tune these tasks. No existing algorithm was deliberately degraded. No defect or AI-search advantage is claimed.

Ten local solver calls were started and completed, nine tasks plus one replay, against a cap of 12. The run used SciPy 1.17.0, NumPy 2.3.5 and Python 3.12.14. Network connections and provider credential loading were blocked by the runner; no provider request or cloud job occurred. This count describes global path computations, not physical time-stepped robot simulations. Reported solver times exclude graph construction/loading and are not speed-comparison evidence.

## Why this is useful

WorldFuzz imports existing tasks, executes an external solver, then compares the actual route with reference answers supplied independently by the dataset. A route can fail for a blocked cell, illegal jump, corner cut, wrong endpoint or a legal but longer-than-reference path. The report preserves exactly which task was run and the full output route, so future implementations can be checked against the same cases. This checkpoint establishes a known-good integration for SciPy Dijkstra; it does not establish market demand or a newly found algorithm defect.

## Inspect and repeat

Open `report.html` for the original maps, recorded-route playback, individual checks, full scenario identity and published/measured length comparison. The same recording is the dashboard's `/benchmarks` page. The JSON download contains all case results and source metadata. Complete original inputs, selection, provenance and result hashes are stored beside this README.

From the matching WorldFuzz checkout:

```bash
python -m pip install -e ".[benchmarks]"
python scripts/run-published-benchmarks.py --out artifacts/new-published-run
```

The command initiates up to 12 new local solver calls and requires a new output directory. It does not read API keys or make network requests. The existing report is a recording, not a live browser execution. Checker tests use small isolated rule fixtures; those fixtures are not the evaluation cases. Four input/checker tests passed, the actual report script's case-selection/playback controls passed with mocked DOM/canvas, and the dashboard served the real report over local HTTP. New visual browser and Windows acceptance were not performed.

## Native benchmark semantics and attribution

This is global path planning for a point agent on the original 256×256 grids. Cardinal cost is 1, diagonal cost sqrt(2), and both adjacent cardinal cells must be free for a diagonal. No metres-per-cell, body radius, dynamics or robot mission deadline is supplied by this benchmark. These conditions are not replaced with the DWA simulator's different assumptions. See the [official file format](https://www.movingai.com/benchmarks/formats.html).

Attribution: Moving AI Lab, Nathan Sturtevant; city/street data provided by Konstantin Yakovlev and Anton Andreychuk. Data is available under [Open Data Commons Attribution License v1.0](https://opendatacommons.org/licenses/by/1-0/), separately from WorldFuzz's code license. Cite N. Sturtevant, “Benchmarks for Grid-Based Pathfinding,” IEEE TCIAIG 4(2), 144–148, 2012. Original archive URLs and SHA-256 values are in `provenance.json`.
