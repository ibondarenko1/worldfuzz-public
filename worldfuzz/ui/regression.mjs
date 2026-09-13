import {fields, fieldValues, applyFields} from './guided-project.mjs';
const $ = id => document.getElementById(id);
const state = {token: '', example: null, runs: [], active: null, selected: null, detail: null, time: 0, playing: false, lastFrame: 0, poll: null};
state.tasks = [];
const terminal = new Set(['COMPLETED', 'CANCELLED', 'TIME_LIMIT_REACHED', 'FAILED', 'INTERRUPTED', 'ERROR']);
const colors = {baseline: '#65d6ec', candidate: '#ff987e', text: '#a2b1c0', grid: '#21313f', lime: '#baff4a'};
function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
function node(tag, text, className) { const result = document.createElement(tag); if (text != null) result.textContent = text; if (className) result.className = className; return result; }
function pretty(value) { return String(value ?? '').replaceAll('_', ' ').toLowerCase(); }
function format(value, places = 3) { return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(places) : '—'; }
async function api(path, body) {
  const response = await fetch('/api/regressions' + path, body === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-WorldFuzz-Token': state.token}, body: JSON.stringify(body)
  });
  let data; try { data = await response.json(); } catch { throw new Error('The local server returned an unreadable response.'); }
  if (!response.ok) throw new Error(data.error || 'The local operation could not complete.');
  return data;
}
function setProject(project, taskId = 'custom') {
  const values = fieldValues(project);
  for (const [id, value] of Object.entries(values)) $(id).value = value;
  $('task-select').value = taskId;
  $('task-description').textContent = state.tasks.find(task => task.id === taskId)?.description || 'Your imported or saved scenario. Changing the fields keeps this map and its unexposed planner settings.';
  $('project-json').value = JSON.stringify(project, null, 2);
  $('project-name').value = project.name ?? '';
  $('search-budget').value = project.search?.budget ?? '';
  $('reduction-budget').value = project.reduction?.budget ?? '';
  updateBudget();
  drawTask(project);
}
function projectValue() {
  let project;
  try { project = JSON.parse($('project-json').value); } catch { throw new Error('Project JSON is invalid. Check commas, quotes and brackets.'); }
  if (!project || Array.isArray(project) || typeof project !== 'object') throw new Error('The project must be a JSON object.');
  project.name = $('project-name').value;
  if (!project.search || !project.reduction) throw new Error('The project needs search and reduction settings.');
  project.search.budget = Number($('search-budget').value);
  project.reduction.budget = Number($('reduction-budget').value);
  return applyFields(project, Object.fromEntries(Object.keys(fields).map(id => [id, $(id).value])));
}
function updateBudget() {
  const pairs = Number($('search-budget').value) + Number($('reduction-budget').value);
  $('simulation-cap').textContent = Number.isFinite(pairs) && pairs >= 0 ? 2 * pairs : '—';
}
function syncFieldChanges() {
  updateBudget();
  try { const project = projectValue(); $('project-json').value = JSON.stringify(project, null, 2); drawTask(project); } catch { /* Preserve unfinished editor content. */ }
}
function renderListing() {
  $('run-list').replaceChildren();
  if (!state.runs.length) $('run-list').append(node('p', 'Your completed and partial runs will appear here.', 'field-note'));
  for (const run of state.runs) {
    const button = node('button', null, 'run-button' + (run.id === state.selected ? ' selected' : ''));
    button.type = 'button'; button.append(node('span', run.title), node('small', pretty(run.status)));
    button.addEventListener('click', () => select(run.id).catch(error => notice(error.message)));
    $('run-list').append(button);
  }
  const busy = Boolean(state.active);
  $('start').disabled = busy || !state.token;
  $('activity').hidden = !busy;
  $('cancel').hidden = !busy || state.active.kind !== 'regression_search';
  if (busy) {
    $('activity-label').textContent = state.active.kind === 'regression_search' ? 'Comparing configurations' : 'Local operation running';
    const current = state.runs.find(run => run.id === state.active.id);
    const count = current?.counts?.completed_simulations ?? 0;
    const limit = current?.limits?.local_simulations ?? null;
    $('progress').max = limit || Math.max(count + 1, 1); $('progress').value = count;
    $('activity-count').textContent = current ? `${count} simulations completed. Cancellation keeps finished work.` : 'This workspace allows one local operation at a time.';
  }
}
async function refresh() {
  const listing = await api(''); state.runs = listing.runs; state.active = listing.activity;
  renderListing();
  if (state.selected) await loadDetail();
  clearTimeout(state.poll);
  state.poll = setTimeout(() => refresh().catch(error => { notice(error.message); state.poll = setTimeout(() => refresh().catch(error => notice(error.message)), 2500); }), state.active ? 650 : 2500);
}
async function select(id) {
  if (state.selected !== id) { state.playing = false; state.time = 0; state.detail = null; }
  state.selected = id; notice(''); renderListing(); await loadDetail();
}
async function loadDetail() {
  const id = state.selected; const detail = await api('/' + id);
  if (id !== state.selected) return;
  state.detail = detail; renderDetail();
}
function duration() {
  const pair = state.detail?.selected;
  return Math.max(pair?.baseline?.summary?.duration_s || 0, pair?.candidate?.summary?.duration_s || 0, 0.05);
}
function metrics(side, run) {
  const summary = run.summary, box = $(side + '-metrics'); box.replaceChildren();
  const clearance = node('div');
  clearance.append(node('p', 'Minimum body clearance', 'metric-label'), node('p', summary.minimum_clearance_m === null ? 'No obstacles' : `${format(summary.minimum_clearance_m)} m`, 'metric-value'), node('p', summary.clearance_passed ? 'Requirement held' : 'Requirement violated', 'metric-state ' + (summary.clearance_passed ? 'good' : 'danger')));
  const mission = node('div');
  mission.append(node('p', 'Goal reached', 'metric-label'), node('p', summary.mission_completed ? 'Completed' : 'Incomplete', 'metric-value ' + (summary.mission_completed ? 'good' : 'warning')), node('p', summary.mission_completed ? `${format(summary.completion_time_s, 2)} s to goal` : `${format(summary.duration_s, 2)} s recorded`, 'metric-state'));
  box.append(clearance, mission);
}
function renderVerification() {
  const data = state.detail, report = data?.verification;
  let text = 'Playback uses stored traces. Verification reruns the selected pair.';
  if (report) {
    const count = report.completed_simulations ?? report.local_simulations ?? 0;
    if (report.status === 'VERIFIED') text = `Replay matched. ${count} local simulations; ${pretty(report.scope || 'selected regression pair')}.`;
    else if (report.status === 'RUNNING') text = `Verifying recorded results. ${count} local simulations completed.`;
    else text = `Verification ${pretty(report.status)}. ${report.message || report.error || 'Inspect the preserved evidence.'}`;
  }
  for (const id of ['verification', 'journal-verification']) $(id).textContent = text;
  for (const id of ['verify', 'journal-verify']) $(id).disabled = Boolean(state.active) || !data || !terminal.has(data.status);
  for (const id of ['download', 'journal-download']) {
    $(id).href = `/api/regressions/${state.selected}/download`;
    $(id).download = `worldfuzz-${state.selected}.zip`;
    $(id).setAttribute('aria-disabled', String(Boolean(data?.activity)));
  }
}
function renderDetail() {
  const data = state.detail, pair = data.selected, counts = data.counts;
  $('result-title').textContent = data.title;
  $('result-state').hidden = false; $('result-state').textContent = pretty(data.status);
  $('result-state').className = 'badge' + (data.status === 'FAILED' ? ' danger' : '');
  const regressions = counts?.regression_pairs ?? 0;
  $('result-description').textContent = data.error ? String(data.error) : !terminal.has(data.status) ? 'Comparing the reference and proposed configurations in matched local scenarios.' : regressions ? `${regressions} search pairs exposed a regression. Inspect the saved case below.` : 'No regression found in the completed search pairs. The bounded result and any completed work are preserved.';
  if (pair && terminal.has(data.status) && !data.error && !regressions) {
    const a = pair.baseline.summary, b = pair.candidate.summary;
    if (!a.mission_completed && !b.mission_completed) $('result-description').textContent = 'No new regression, but both configurations missed the goal deadline in the displayed case. This task has not passed.';
    else if (!a.clearance_passed && !b.clearance_passed) $('result-description').textContent = 'No new regression, but both configurations violated required clearance in the displayed case. This task has not passed.';
  }
  $('empty').hidden = true; $('run-summary').hidden = false; $('run-summary').replaceChildren();
  for (const [key, label] of [['completed_pairs', 'search pairs'], ['regression_pairs', 'regressions'], ['search_simulations', 'search simulations'], ['reduction_simulations', 'reduction simulations'], ['provider_calls', 'API calls']]) {
    const item = node('span'); item.append(node('b', String(counts?.[key] ?? 0)), document.createTextNode(' ' + label)); $('run-summary').append(item);
  }
  $('replay').hidden = !pair; $('no-pair').hidden = Boolean(pair);
  if (pair) {
    $('baseline-name').textContent = data.project.baseline.label;
    $('candidate-name').textContent = data.project.candidate.label;
    $('finding-label').textContent = pair.regressions?.length ? pair.regressions.map(pretty).join(' · ') : 'No regression in this pair';
    metrics('baseline', pair.baseline); metrics('candidate', pair.candidate);
    $('case-title').textContent = pair.reduced ? 'Reduced regression case' : 'Selected comparison case';
    $('case-explanation').textContent = pair.reduced ? 'Obstacle removal preserved the recorded regression. This is the reduced result reached within the reduction budget.' : pair.regressions?.length ? 'This case preserves the measured regression. Reduction may not remove every unnecessary obstacle within its budget.' : 'No regression was found in this pair. It remains available for inspection and replay.';
    const details = [['Required clearance', `${format(data.project.requirements.minimum_clearance_m)} m`], ['Obstacles kept', pair.scenario.obstacles.length], ['Obstacles removed', pair.removed_obstacles ?? 0], ['Goal', pair.scenario.goal.map(value => format(value, 2)).join(', ') + ' m'], ['Scenario seed', pair.scenario.seed]];
    $('case-details').replaceChildren(); for (const [label, value] of details) $('case-details').append(node('dt', label), node('dd', String(value)));
    state.time = Math.min(state.time, duration()); $('timeline').max = duration(); $('end-time').textContent = format(duration(), 1) + ' s'; draw();
  } else {
    $('no-pair-description').textContent = data.error ? String(data.error) : 'The operation journal preserves any completed work. A paired replay appears after both configurations finish.';
  }
  renderVerification();
}
function drawTask(project) {
  const canvas = $('task-preview'), ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const scene = project.scenario;
  const points = [scene.start, scene.goal, ...scene.obstacles];
  if (points.some(p => !Array.isArray(p) || p.length < 2 || p.slice(0, 2).some(v => typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > 20))) {
    $('map-summary').textContent = 'Preview unavailable. Coordinates must be finite numbers between -20 and 20 m.'; return;
  }
  const padding = Math.max(1, scene.robot_radius_m + .3);
  const xmin = Math.min(...points.map(p => p[0])) - padding, xmax = Math.max(...points.map(p => p[0])) + padding;
  const ymin = Math.min(...points.map(p => p[1])) - padding, ymax = Math.max(...points.map(p => p[1])) + padding;
  const scale = Math.min(560 / (xmax - xmin), 230 / (ymax - ymin));
  const x = v => 320 + (v - (xmin + xmax) / 2) * scale, y = v => 150 - (v - (ymin + ymax) / 2) * scale;
  ctx.font = '18px system-ui'; ctx.textAlign = 'center';
  for (const p of scene.obstacles) { ctx.beginPath(); ctx.arc(x(p[0]), y(p[1]), 5, 0, Math.PI * 2); ctx.fillStyle = '#d3dce4'; ctx.fill(); }
  for (const [point, label, color, radius] of [[scene.start, 'START', colors.baseline, scene.robot_radius_m], [scene.goal, 'GOAL', colors.lime, scene.goal_tolerance_m]]) {
    ctx.beginPath(); ctx.arc(x(point[0]), y(point[1]), Math.max(3, radius * scale), 0, Math.PI * 2); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = color; ctx.fillText(label, x(point[0]), Math.min(300, y(point[1]) + radius * scale + 24));
  }
  $('map-summary').textContent = `${scene.obstacles.length} static obstacle points. Goal: ${scene.goal.join(', ')} m. Preview only, no simulation has run.`;
}
function drawScene(side, bounds) {
  const pair = state.detail.selected, scenario = pair.scenario, run = pair[side], trace = run.trace;
  const canvas = $(side + '-scene'), ctx = canvas.getContext('2d'), W = canvas.width, H = canvas.height;
  const factor = W / Math.max(canvas.clientWidth, 250), padding = 30 * factor;
  const scale = Math.min((W - 2 * padding) / bounds.width, (H - 2 * padding) / bounds.height);
  const cx = W / 2, cy = H / 2, x = value => cx + (value - bounds.cx) * scale, y = value => cy - (value - bounds.cy) * scale;
  ctx.clearRect(0, 0, W, H); ctx.fillStyle = '#0d1620'; ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = colors.grid; ctx.lineWidth = factor * 0.6;
  const increment = bounds.width > 20 ? 5 : bounds.width > 10 ? 2 : 1;
  for (let value = Math.ceil(bounds.xmin / increment) * increment; value <= bounds.xmax; value += increment) { ctx.beginPath(); ctx.moveTo(x(value), padding); ctx.lineTo(x(value), H - padding); ctx.stroke(); }
  for (let value = Math.ceil(bounds.ymin / increment) * increment; value <= bounds.ymax; value += increment) { ctx.beginPath(); ctx.moveTo(padding, y(value)); ctx.lineTo(W - padding, y(value)); ctx.stroke(); }
  ctx.font = `${11 * factor}px system-ui`; ctx.fillStyle = colors.text; ctx.textAlign = 'left'; ctx.fillText(`Grid ${increment} m`, padding, H - 9 * factor);
  for (const obstacle of scenario.obstacles) {
    ctx.beginPath(); ctx.arc(x(obstacle[0]), y(obstacle[1]), 4.5 * factor, 0, 2 * Math.PI); ctx.fillStyle = '#d3dce4'; ctx.fill();
  }
  const goal = scenario.goal;
  ctx.beginPath(); ctx.arc(x(goal[0]), y(goal[1]), scenario.goal_tolerance_m * scale, 0, 2 * Math.PI); ctx.strokeStyle = '#709a38'; ctx.lineWidth = factor; ctx.stroke();
  ctx.fillStyle = colors.lime; ctx.fillText('GOAL', x(goal[0]) + 8 * factor, y(goal[1]) - 8 * factor);
  ctx.beginPath(); ctx.moveTo(x(goal[0]) - 4 * factor, y(goal[1])); ctx.lineTo(x(goal[0]) + 4 * factor, y(goal[1])); ctx.moveTo(x(goal[0]), y(goal[1]) - 4 * factor); ctx.lineTo(x(goal[0]), y(goal[1]) + 4 * factor); ctx.strokeStyle = colors.lime; ctx.stroke();
  if (!trace.length) return;
  ctx.beginPath(); trace.forEach((point, index) => index ? ctx.lineTo(x(point.x), y(point.y)) : ctx.moveTo(x(point.x), y(point.y))); ctx.strokeStyle = side === 'baseline' ? '#254956' : '#54392f'; ctx.lineWidth = 2 * factor; ctx.stroke();
  let last = trace[0]; ctx.beginPath(); ctx.moveTo(x(last.x), y(last.y));
  for (const point of trace) { if (point.t > state.time + 1e-7) break; ctx.lineTo(x(point.x), y(point.y)); last = point; }
  ctx.strokeStyle = colors[side]; ctx.lineWidth = 2.5 * factor; ctx.stroke();
  const robotRadius = scenario.robot_radius_m * scale;
  ctx.beginPath(); ctx.arc(x(last.x), y(last.y), robotRadius, 0, 2 * Math.PI); ctx.fillStyle = side === 'baseline' ? '#153946' : '#493127'; ctx.fill(); ctx.strokeStyle = colors[side]; ctx.lineWidth = 2 * factor; ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x(last.x), y(last.y)); ctx.lineTo(x(last.x) + Math.cos(last.yaw) * robotRadius * 1.35, y(last.y) - Math.sin(last.yaw) * robotRadius * 1.35); ctx.stroke();
  $(side + '-live').textContent = `${format(last.v, 2)} m/s · ${format(last.x, 2)}, ${format(last.y, 2)} m` + (state.time >= trace.at(-1).t ? ' · Recorded endpoint' : '');
}
function draw() {
  if (!state.detail?.selected) return;
  const pair = state.detail.selected, points = [pair.scenario.start, pair.scenario.goal, ...pair.scenario.obstacles, ...pair.baseline.trace.map(point => [point.x, point.y]), ...pair.candidate.trace.map(point => [point.x, point.y])];
  const xmin = Math.min(...points.map(point => point[0])) - 1.1, xmax = Math.max(...points.map(point => point[0])) + 1.1;
  const ymin = Math.min(...points.map(point => point[1])) - 1.1, ymax = Math.max(...points.map(point => point[1])) + 1.1;
  const bounds = {xmin, xmax, ymin, ymax, cx: (xmin + xmax) / 2, cy: (ymin + ymax) / 2, width: xmax - xmin, height: ymax - ymin};
  drawScene('baseline', bounds); drawScene('candidate', bounds);
  $('timeline').value = state.time; $('clock').textContent = format(state.time, 2) + ' s'; $('play').textContent = state.playing ? 'Pause' : 'Play';
  $('play').setAttribute('aria-label', state.playing ? 'Pause recorded paths' : 'Play recorded paths');
}
function animate(stamp) {
  if (state.playing && state.detail?.selected) {
    state.time = Math.min(duration(), state.time + Math.min((stamp - state.lastFrame) / 1000, 0.1) * Number($('speed').value));
    if (state.time >= duration()) state.playing = false;
    draw();
  }
  state.lastFrame = stamp; requestAnimationFrame(animate);
}
$('project-form').addEventListener('submit', async event => {
  event.preventDefault(); notice(''); $('start').disabled = true;
  try { const result = await api('', projectValue()); await select(result.id); await refresh(); } catch (error) { notice(error.message); $('start').disabled = Boolean(state.active); }
});
$('project-json').addEventListener('change', () => { try { setProject(JSON.parse($('project-json').value)); notice(''); } catch (error) { notice(error.message); } });
for (const id of ['project-name', 'search-budget', 'reduction-budget', ...Object.keys(fields)]) $(id).addEventListener('input', syncFieldChanges);
$('task-select').addEventListener('change', () => {
  const task = state.tasks.find(item => item.id === $('task-select').value);
  if (task) { setProject(task.project, task.id); notice('Task loaded. Review the map, requirements and two configurations before running.'); }
  else $('task-description').textContent = 'Custom scenario. Current map and settings are preserved.';
});
$('reset-project').addEventListener('click', () => {
  const task = state.tasks.find(item => item.id === $('task-select').value);
  if (task) { setProject(task.project, task.id); notice('Task defaults restored.'); }
  else notice('Choose a built-in task to load its defaults. Your custom map has been preserved.');
});
$('reuse-case').addEventListener('click', () => {
  if (!state.detail?.selected) return;
  const project = structuredClone(state.detail.project);
  project.scenario = structuredClone(state.detail.selected.scenario);
  project.search.budget = 1; project.search.position_jitter_m = 0; project.reduction.budget = 0;
  setProject(project);
  notice('Saved case loaded as a new 1-pair comparison. Edit the proposed configuration, then run. Original evidence stays unchanged.');
  $('proposed-radius').focus();
});
$('project-file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  try { if (file.size > 65536) throw new Error('Project JSON must be at most 64 KiB.'); const value = JSON.parse(await file.text()); setProject(value); notice('Project loaded. Review the two configurations, then start the comparison.'); } catch (error) { notice(error instanceof SyntaxError ? 'The selected file is not valid JSON.' : error.message); }
  event.target.value = '';
});
$('save-project').addEventListener('click', () => { try { const url = URL.createObjectURL(new Blob([JSON.stringify(projectValue(), null, 2) + '\n'], {type: 'application/json'})); const link = node('a'); link.href = url; link.download = 'worldfuzz-navigation-project.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); } catch (error) { notice(error.message); } });
$('refresh').addEventListener('click', () => refresh().catch(error => notice(error.message)));
$('cancel').addEventListener('click', async () => { if (!state.active) return; try { await api('/' + state.active.id + '/cancel', {}); notice('Cancellation requested. Finished work remains in the journal.'); await refresh(); } catch (error) { notice(error.message); } });
for (const id of ['verify', 'journal-verify']) $(id).addEventListener('click', async () => { try { await api('/' + state.selected + '/verify', {}); notice(''); await refresh(); } catch (error) { notice(error.message); } });
$('play').addEventListener('click', () => { if (state.time >= duration()) state.time = 0; state.playing = !state.playing; draw(); });
$('timeline').addEventListener('input', event => { state.time = Number(event.target.value); state.playing = false; draw(); });
$('end').addEventListener('click', () => { state.time = duration(); state.playing = false; draw(); });
window.addEventListener('resize', () => { draw(); try { drawTask(projectValue()); } catch {} });
requestAnimationFrame(animate);
try { const boot = await api('/bootstrap'); state.token = boot.csrf_token; state.example = boot.default_project; state.tasks = boot.tasks || []; for (const task of state.tasks) { const option = node('option', task.title); option.value = task.id; $('task-select').append(option); } state.runs = boot.runs; state.active = boot.activity; setProject(state.tasks[0]?.project || state.example, state.tasks[0]?.id || 'custom'); renderListing(); if (state.runs.length) await select(state.runs[0].id); await refresh(); } catch (error) { notice(error.message); }
