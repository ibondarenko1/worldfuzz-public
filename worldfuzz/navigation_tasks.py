"""Built-in software tasks. These examples are not held-out benchmarks."""
from .regression import default_project, validate_project


def task_catalog():
    tasks = []
    for task_id, title, description in (
        ('narrow-passage', 'Narrow passage', 'Reach the goal through two obstacle markers. Compare how planning clearance affects completion.'),
        ('obstacle-detour', 'Obstacle detour', 'Navigate around a cluster of static obstacle points while keeping the required body clearance.'),
        ('timed-delivery', 'Timed delivery', 'Reach a goal six metres away before the deadline. Compare a reference speed with a slower proposal.'),
    ):
        project = default_project()
        project['name'] = title
        project['search'].update(budget=1, position_jitter_m=0)
        project['reduction']['budget'] = 0
        if task_id == 'obstacle-detour':
            project['scenario'].update(obstacles=[[2, -.45], [2, 0], [2, .45]], horizon_s=16)
            project['candidate']['parameters']['planning_radius_m'] = .5
        elif task_id == 'timed-delivery':
            project['scenario'].update(goal=[6, 0], obstacles=[], horizon_s=8)
            project['candidate']['parameters'].update(planning_radius_m=.35, max_speed_mps=.45)
        tasks.append({'id': task_id, 'title': title, 'description': description,
                      'project': validate_project(project)})
    return tasks
