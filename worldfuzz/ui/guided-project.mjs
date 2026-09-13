// Patch only visible fields. Imported maps and unexposed planner settings survive.
export const fields = {
  'robot-radius': ['scenario', 'robot_radius_m'],
  'required-clearance': ['requirements', 'minimum_clearance_m'],
  'mission-deadline': ['scenario', 'horizon_s'],
  'position-variation': ['search', 'position_jitter_m'],
  'reference-radius': ['baseline', 'parameters', 'planning_radius_m'],
  'proposed-radius': ['candidate', 'parameters', 'planning_radius_m'],
  'reference-speed': ['baseline', 'parameters', 'max_speed_mps'],
  'proposed-speed': ['candidate', 'parameters', 'max_speed_mps'],
};
export function fieldValues(project) {
  return Object.fromEntries(Object.entries(fields).map(([id, path]) => {
    const value = path.reduce((parent, key) => parent?.[key], project);
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('Project is missing a valid ' + id.replaceAll('-', ' ') + '.');
    return [id, value];
  }));
}
export function applyFields(project, values) {
  const result = structuredClone(project);
  fieldValues(result);
  for (const [id, path] of Object.entries(fields)) {
    const raw = values[id];
    if (raw === undefined || String(raw).trim() === '' || !Number.isFinite(Number(raw))) throw new Error('Enter a number for ' + id.replaceAll('-', ' ') + '.');
    const parent = path.slice(0, -1).reduce((object, key) => object[key], result);
    parent[path.at(-1)] = Number(raw);
  }
  return result;
}
