"""Published Moving AI grid cases, in their original benchmark semantics.

This evaluates a global grid planner, not the circular-body DWA simulation.
"""
import hashlib
import importlib
import json
import math
from pathlib import Path
import shutil
import sys
import time

from .engine import write_json
from .experiment import now

DATA = Path(__file__).parent / 'benchmark_data'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_cases():
    provenance = json.loads((DATA / 'provenance.json').read_text())
    selection = json.loads((DATA / 'selection.json').read_text())
    for source in provenance['sources']:
        if Path(source['file']).name != source['file'] or sha(DATA / source['file']) != source['sha256']:
            raise ValueError('Published input does not match its recorded source hash')
    maps, cases = {}, []
    for item in selection['selected']:
        name = item['scenario_file']
        if Path(name).name != name:
            raise ValueError('Invalid scenario filename')
        lines = (DATA / name).read_text().splitlines()
        if lines[0] not in ('version 1', 'version 1.0') or lines[item['data_row']] != item['raw_record']:
            raise ValueError('Selected row does not match the published file')
        fields = item['raw_record'].split()
        if len(fields) != 9:
            raise ValueError('Expected the original nine-column scenario format')
        bucket, map_name, width, height, sx, sy, gx, gy, optimal = fields
        width, height, sx, sy, gx, gy = map(int, (width, height, sx, sy, gx, gy))
        if Path(map_name).name != map_name or width * height > selection['max_cells_per_map']:
            raise ValueError('Unsupported map or map size')
        if map_name not in maps:
            map_lines = (DATA / map_name).read_text().splitlines()
            if map_lines[:4] != ['type octile', f'height {height}', f'width {width}', 'map']:
                raise ValueError('Map dimensions differ; resizing is not permitted')
            rows = map_lines[4:]
            if len(rows) != height or any(len(row) != width or set(row) - {'.', '@'} for row in rows):
                raise ValueError('This adapter only supports the selected binary street-map terrain')
            maps[map_name] = rows
        rows = maps[map_name]
        for x, y in [(sx, sy), (gx, gy)]:
            if not 0 <= x < width or not 0 <= y < height or rows[y][x] != '.':
                raise ValueError('Published endpoint is outside passable terrain')
        optimal = float(optimal)
        if not math.isfinite(optimal) or optimal < 0:
            raise ValueError('Invalid published optimal length')
        cases.append({**item, 'map': map_name, 'width': width, 'height': height,
                      'start': [sx, sy], 'goal': [gx, gy], 'published_length': optimal})
    return provenance, selection, maps, cases


def grid_graph(rows):
    import numpy as np
    from scipy.sparse import csr_matrix
    height, width = len(rows), len(rows[0])
    source, target, costs = [], [], []
    for y in range(height):
        for x in range(width):
            if rows[y][x] != '.':
                continue
            for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 1)):
                nx, ny = x + dx, y + dy
                if not 0 <= nx < width or not 0 <= ny < height or rows[ny][nx] != '.':
                    continue
                if dx and dy and (rows[y][nx] != '.' or rows[ny][x] != '.'):
                    continue
                a, b = y * width + x, ny * width + nx
                source.extend((a, b)); target.extend((b, a))
                cost = math.sqrt(2) if dx and dy else 1
                costs.extend((cost, cost))
    return csr_matrix((np.asarray(costs), (np.asarray(source, dtype=np.int32), np.asarray(target, dtype=np.int32))),
                      shape=(width * height, width * height))


def solve(graph, case):
    from scipy.sparse.csgraph import dijkstra
    width = case['width']
    start = case['start'][1] * width + case['start'][0]
    goal = case['goal'][1] * width + case['goal'][0]
    distances, predecessors = dijkstra(graph, directed=True, indices=start, return_predecessors=True)
    if not math.isfinite(float(distances[goal])):
        return []
    route, current = [], goal
    for _ in range(graph.shape[0] + 1):
        route.append([current % width, current // width])
        if current == start:
            return route[::-1]
        current = int(predecessors[current])
        if current < 0:
            break
    raise ValueError('Solver returned an invalid predecessor chain')


def check_route(rows, case, route, tolerance=1e-5):
    """Independent cell/step checks, without using graph edges or solver distances."""
    errors, total = [], 0.0
    if not route or route[0] != case['start'] or route[-1] != case['goal']:
        errors.append('ENDPOINT_MISMATCH')
    previous = None
    for point in route:
        if not isinstance(point, list) or len(point) != 2 or any(type(v) is not int for v in point):
            errors.append('INVALID_POINT'); break
        x, y = point
        if not 0 <= x < case['width'] or not 0 <= y < case['height'] or rows[y][x] != '.':
            errors.append('BLOCKED_CELL'); break
        if previous is not None:
            px, py = previous
            dx, dy = abs(x - px), abs(y - py)
            if max(dx, dy) != 1:
                errors.append('INVALID_STEP'); break
            if dx and dy and (rows[py][x] != '.' or rows[y][px] != '.'):
                errors.append('CORNER_CUT'); break
            total += math.sqrt(2) if dx and dy else 1
        previous = point
    length = None if errors else total
    delta = None if length is None else length - case['published_length']
    matches = delta is not None and abs(delta) <= tolerance
    return {'route_valid': not errors, 'errors': errors, 'measured_length': length,
            'difference_from_published': delta, 'matches_published_optimum': matches,
            'verdict': 'MATCH' if matches else 'INVALID_ROUTE' if errors else 'LENGTH_MISMATCH'}


def render_report(report, maps):
    template = (Path(__file__).parent / 'ui' / 'benchmark-report-template.html').read_text()
    data = json.dumps({'report': report, 'maps': maps}, ensure_ascii=True).replace('<', '\\u003c')
    return template.replace('__BENCHMARK_JSON__', data)


def run_published(out):
    import numpy
    import scipy
    provenance, selection, maps, cases = load_cases()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / 'inputs').mkdir()
    for source in provenance['sources']:
        shutil.copyfile(DATA / source['file'], out / 'inputs' / source['file'])
    write_json(out / 'selection.json', selection)
    write_json(out / 'provenance.json', provenance)
    report = {'schema_version': 1, 'status': 'RUNNING', 'recorded_at': now(),
              'source_sha256': sha(__file__), 'provenance': provenance, 'limits': selection,
              'solver': 'SciPy Dijkstra', 'python': sys.version.split()[0],
              'dependencies': {'numpy': numpy.__version__, 'scipy': scipy.__version__},
              'solver_binary_sha256': sha(importlib.import_module('scipy.sparse.csgraph._shortest_path').__file__),
              'started_solver_calls': 0, 'completed_solver_calls': 0, 'provider_calls': 0,
              'cloud_jobs': 0, 'cases': [], 'replay': None, 'error': None}
    started = time.monotonic()
    graphs = {}
    def save():
        write_json(out / 'report.json', report)
    def execute(case):
        if report['started_solver_calls'] >= selection['solver_calls'] or time.monotonic() - started > 180:
            raise RuntimeError('Execution budget exhausted; no retry')
        if case['map'] not in graphs:
            graphs[case['map']] = grid_graph(maps[case['map']])
        report['started_solver_calls'] += 1
        save()
        begin = time.perf_counter()
        route = solve(graphs[case['map']], case)
        elapsed = time.perf_counter() - begin
        report['completed_solver_calls'] += 1
        save()
        return route, elapsed
    save()
    try:
        for case in cases:
            record = {'case': case, 'status': 'RUNNING'}
            report['cases'].append(record)
            save()
            route, elapsed = execute(case)
            record.update(status='COMPLETED', route=route, solver_seconds=elapsed,
                          check=check_route(maps[case['map']], case, route, selection['optimal_length_tolerance']))
            save()
        route, elapsed = execute(cases[0])
        report['replay'] = {'case_index': 0, 'same_route': route == report['cases'][0]['route'],
                            'solver_seconds': elapsed, 'check': check_route(maps[cases[0]['map']], cases[0], route)}
        report['status'] = 'COMPLETED'
    except (Exception, KeyboardInterrupt) as exc:
        report['status'] = 'CANCELLED' if isinstance(exc, KeyboardInterrupt) else 'FAILED'
        report['error'] = type(exc).__name__ + ': ' + str(exc)[:250]
    report['finished_at'] = now()
    save()
    (out / 'report.html').write_text(render_report(report, maps))
    files = sorted(p for p in out.rglob('*') if p.is_file())
    write_json(out / 'manifest.json', {'files': {p.relative_to(out).as_posix(): sha(p) for p in files}})
    return report
