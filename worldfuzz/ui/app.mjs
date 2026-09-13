const $ = id => document.getElementById(id);
const state = {token: null, cases: [], selected: null, detail: null, list: null, active: null,
  time: 0, playing: false, previousFrame: null, pairKey: null, timer: null, selecting: 0};
const labels = {baseline: 'Baseline', protective_distance: 'Protective distance', hardened: 'Hardened',
  QUEUED: 'Queued', RUNNING: 'Running', COMPLETED: 'Completed', CANCELLED: 'Cancelled',
  FAILED: 'Failed', TIME_LIMIT_REACHED: 'Time limit reached', INTERRUPTED: 'Interrupted', RECORDED: 'Recorded'};
function node(tag, text, className) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; }
function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-WorldFuzz-Token': state.token}, body: JSON.stringify(data)});
  let value;
  try { value = await response.json(); } catch { throw new Error('The local server returned an unreadable response. Refresh to reconnect.'); }
  if (!response.ok) throw new Error(value.error || 'The local operation could not be completed.');
  return value;
}
function downloadURL(id) { return `/api/experiments/${encodeURIComponent(id)}/download`; }
function runButtons(list) {
  const target = $('run-list'); target.replaceChildren();
  for (const [title, items] of [['Your experiments', list.runs], ['Recorded evidence', list.recordings]]) {
    if (items.length) target.append(node('p', title, 'run-group'));
    for (const item of items) {
      const button = node('button', null, 'run-button' + (state.selected === item.id ? ' selected' : ''));
      button.type = 'button'; button.setAttribute('aria-current', state.selected === item.id ? 'true' : 'false');
      button.append(node('span', item.title));
      const meta = item.origin === 'recording' ? item.description.replace('Recorded tuning pilot. ', '') :
        `${labels[item.status] || item.status} · ${item.counts?.scenario_pairs ?? 0} pairs`;
      button.append(node('small', meta)); button.addEventListener('click', () => select(item.id).catch(showError)); target.append(button);
    }
  }
}
function activity(list) {
  state.active = list.activity;
  const job = list.runs.find(r => r.id === list.activity?.id);
  const isSearch = list.activity?.kind === 'search';
  $('start').disabled = !!list.activity || !state.token;
  $('activity').hidden = !list.activity;
  $('cancel').hidden = !isSearch; $('cancel').disabled = false;
  $('activity-label').textContent = isSearch ? 'Searching locally' : 'Verifying evidence';
  if (isSearch && job) {
    $('progress').value = job.counts?.completed_simulations || 0;
    $('progress').max = job.limits?.local_simulations || 1;
    $('activity-count').textContent = `${job.counts?.completed_simulations || 0} / ${job.limits?.local_simulations || 0} local simulations`;
  } else { $('progress').removeAttribute('value'); $('activity-count').textContent = 'Rerunning stored scenarios. No API calls.'; }
  $('verify').disabled = !!list.activity;
  $('journal-verify').disabled = !!list.activity;
}
async function refresh() {
  const list = await api('/api/experiments'); state.list = list; runButtons(list); activity(list);
  if (state.selected) await select(state.selected, true);
  clearTimeout(state.timer);
  if (list.activity) state.timer = setTimeout(() => refresh().catch(showError), 450);
}
function showError(error) { notice(error.message || 'The local operation failed.'); }
async function select(id, polling = false) {
  const ticket = ++state.selecting;
  if (!polling) { state.selected = id; notice(); if (state.list) runButtons(state.list); }
  const detail = await api(`/api/experiments/${encodeURIComponent(id)}`);
  if (ticket !== state.selecting || state.selected !== id) return;
  state.detail = detail; renderDetail(detail);
}
function stat(label, value) { const item = node('span'); item.append(node('strong', String(value)), document.createTextNode(' ' + label)); return item; }
function renderDetail(d) {
  $('empty').hidden = true;
  $('origin').textContent = d.origin === 'recording' ? 'RECORDED EVIDENCE / LOCAL PLAYBACK' : 'LOCAL EXPERIMENT / FIXED TUNING TASK';
  $('result-title').textContent = d.title;
  $('result-description').textContent = d.description;
  const found = d.counts?.findings;
  $('result-state').hidden = false;
  $('result-state').textContent = d.status === 'COMPLETED' ? (found ? 'Counterexample found' : 'No counterexample found') : labels[d.status] || d.status;
  $('result-state').className = 'badge' + (d.status === 'COMPLETED' && found ? ' danger' : '');
  $('experiment-summary').hidden = !d.counts;
  if (d.counts) {
    const c = d.counts;
    $('experiment-summary').replaceChildren(stat('scenario pairs', `${c.scenario_pairs}/${d.config.budget}`),
      stat('local simulations', c.completed_simulations), stat('distance violations', c.findings),
      stat('hardened missions completed', `${c.hardened_missions_completed}/${c.scenario_pairs}`));
    if (c.unconfirmed_simulations) $('experiment-summary').append(stat('unconfirmed attempts', c.unconfirmed_simulations));
  }
  $('replay').hidden = !d.pair; $('no-pair').hidden = !!d.pair;
  $('journal-download').href = downloadURL(d.id); $('download').href = downloadURL(d.id);
  for (const id of ['download', 'journal-download']) {
    $(id).setAttribute('aria-disabled', String(d.activity === 'search'));
    $(id).title = d.activity === 'search' ? 'Stop the search before downloading' : 'Download the saved evidence archive';
  }
  renderVerification(d);
  if (!d.pair) {
    $('no-pair-title').textContent = ['RUNNING', 'QUEUED'].includes(d.status) ? 'Waiting for the first pair.' : 'No complete trace pair.';
    $('no-pair-description').textContent = d.error || 'Any completed operations remain in the evidence journal. This is not a safety verdict.';
    state.playing = false; return;
  }
  const key = d.id + d.pair.target.trace_sha256 + d.pair.hardened.trace_sha256;
  if (key !== state.pairKey) {
    state.pairKey = key; state.playing = false; state.time = 0;
    $('target-policy').textContent = labels[d.pair.target.policy] || d.pair.target.policy;
    metrics('target-metrics', d.pair.target); metrics('hardened-metrics', d.pair.hardened);
    const index = d.pair.index;
    $('scenario-label').textContent = index == null ? 'Reduced recorded scenario' : `Selected scenario ${index} · closest complete pair`;
    const s = d.pair.target.scenario, w = d.pair.target.world;
    $('scenario-details').replaceChildren();
    for (const [name, value] of [['Required distance', `${s.safety_distance_m.toFixed(2)} m`],
      ['Robot speed', `${s.robot_speed_mps} m/s`], ['Crossing time', `${s.human_crossing_time_s} s`],
      ['Pedestrian speed', `${s.human_speed_mps} m/s`], ['Crossing position', `${w.human_crossing_x_m.toFixed(3)} m`],
      ['Sensor delay', `${s.sensor_delay_ms} ms`], ['Observation blackout', s.occlusion ? '2–6 s' : 'None'],
      ['Scenario seed', String(s.seed)]]) $('scenario-details').append(node('dt', name), node('dd', value));
    $('timeline').max = w.horizon_s;
    $('jump').disabled = !firstViolation(d.pair.target);
    paint();
  }
}
function renderVerification(d) {
  $('verify').disabled = !!state.active;
  $('journal-verify').disabled = !!state.active;
  $('verify').textContent = d.activity === 'verification' ? 'Verifying…' : 'Verify by rerunning';
  $('journal-verify').textContent = d.activity === 'verification' ? 'Verifying…' : 'Verify journal';
  const v = d.verification;
  const message = v?.status === 'VERIFIED' && v.local_simulations === 0 ? 'Journal validated. No completed simulations to replay.' : v?.status === 'VERIFIED' ?
    `Replay matched. ${v.local_simulations} local simulations${v.scope === 'selected_recorded_pair' ? '; selected pair only' : '; all completed experiment operations'}.${v.source_matches === false ? ' Source revision differs from the recording.' : ''}` :
    v?.status === 'FAILED' ? `Verification failed after ${v.local_simulations ?? 0} local simulations; ${v.unconfirmed_simulations ?? 0} unconfirmed attempts. The evidence was not changed.` :
    v?.status === 'INTERRUPTED' ? `Verification was interrupted after ${v.local_simulations ?? 0} confirmed simulations. No automatic retry.` :
    v?.status === 'RUNNING' ? 'Rerunning the recorded scenarios locally…' :
    `Playback reads stored traces. Verification reruns ${d.origin === 'recording' ? 'this pair (2 simulations)' : 'all completed experiment operations'}.`;
  $('verification').textContent = message;
  $('journal-verification').textContent = message;
}
function metrics(id, run) {
  const s = run.summary, violated = s.violations.some(v => v.monitor === 'human_distance');
  const distance = node('div'), mission = node('div');
  distance.append(node('p', 'Closest approach', 'metric-label'));
  const value = node('p', s.minimum_distance_m == null ? '—' : s.minimum_distance_m.toFixed(3), 'metric-value');
  value.append(node('small', ' m')); distance.append(value, node('p', violated ? 'Distance violated' : 'Distance held', 'metric-state ' + (violated ? 'danger' : 'good')));
  mission.append(node('p', 'Route completion', 'metric-label'), node('p', s.mission_completed ? 'Completed' : 'Timed out', 'metric-value'),
    node('p', s.mission_completed ? 'Goal reached within 20 s' : 'Goal not reached within 20 s', 'metric-state ' + (s.mission_completed ? '' : 'warning')));
  $(id).replaceChildren(distance, mission);
}
function firstViolation(run) { return run.summary.violations.find(v => v.monitor === 'human_distance'); }
function sample(run, t) { const index = Math.min(run.trace.length - 1, Math.max(0, Math.floor(t / run.world.timestep_s + 1e-7))); return run.trace[index]; }
function line(ctx, points, color, width = 2, dashed = false) {
  ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dashed ? [6, 6] : []);
  points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke(); ctx.setLineDash([]);
}
function circle(ctx, x, y, radius, fill, stroke) { ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); if (fill) { ctx.fillStyle = fill; ctx.fill(); } if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 2; ctx.stroke(); } }
function text(ctx, content, x, y, color = '#a2b1c0', align = 'left', size = 15) { ctx.fillStyle = color; const scale = ctx.canvas.width / Math.max(ctx.canvas.clientWidth, 200); ctx.font = `${size * scale}px system-ui, sans-serif`; ctx.textAlign = align; ctx.fillText(content, x, y); }
function drawScene(canvas, run, role) {
  const ctx = canvas.getContext('2d'), width = canvas.width, height = canvas.height;
  ctx.clearRect(0, 0, width, height); const bounds = run.world.bounds;
  const scale = Math.min((width - 76) / (bounds.x_max - bounds.x_min), (height - 72) / (bounds.y_max - bounds.y_min));
  const left = (width - (bounds.x_max - bounds.x_min) * scale) / 2;
  const top = (height - (bounds.y_max - bounds.y_min) * scale) / 2;
  const x = v => left + (v - bounds.x_min) * scale, y = v => top + (bounds.y_max - v) * scale;
  for (let i = bounds.x_min; i <= bounds.x_max; i++) line(ctx, [[x(i), y(bounds.y_min)], [x(i), y(bounds.y_max)]], '#1a2835', 1);
  for (let i = bounds.y_min; i <= bounds.y_max; i++) line(ctx, [[x(bounds.x_min), y(i)], [x(bounds.x_max), y(i)]], '#1a2835', 1);
  line(ctx, [[x(0), y(0)], [x(run.world.goal_x_m), y(0)]], '#445d70', 2, true);
  line(ctx, [[x(run.world.human_crossing_x_m), y(bounds.y_min)], [x(run.world.human_crossing_x_m), y(bounds.y_max)]], '#4d4842', 1, true);
  line(ctx, [[x(run.world.goal_x_m), y(-.42)], [x(run.world.goal_x_m), y(.42)]], '#baff4a', 3);
  text(ctx, 'START', x(0), y(0) + 31, '#8397aa', 'center', 13); text(ctx, 'GOAL', x(run.world.goal_x_m), y(0) + 31, '#baff4a', 'center', 13);
  for (const tick of [0, 4, 8, 12]) text(ctx, String(tick), x(tick), height - 8, '#8093a3', 'center', 13);
  const row = sample(run, state.time), color = role === 'target' ? '#ff987e' : '#65d6ec';
  const violation = row.distance_m != null && row.distance_m < run.scenario.safety_distance_m;
  circle(ctx, x(row.robot_x), y(0), run.scenario.safety_distance_m * scale, violation ? '#ff76502a' : '#65d6ec0c', violation ? '#ff987e' : '#426272');
  line(ctx, [[x(0), y(0)], [x(row.robot_x), y(0)]], color, 3);
  circle(ctx, x(row.robot_x), y(0), 9, color, '#0d1620');
  if (row.human_present) {
    const offscreen = row.human_y > bounds.y_max || row.human_y < bounds.y_min;
    const hy = Math.max(bounds.y_min, Math.min(bounds.y_max, row.human_y));
    circle(ctx, x(row.human_x), y(hy), 7, '#f4eee1', '#0d1620');
    const align = x(row.human_x) > width / 2 ? 'right' : 'left';
    text(ctx, offscreen ? `Offscreen · y ${row.human_y.toFixed(1)} m` : 'Pedestrian', x(row.human_x) + (align === 'left' ? 14 : -14), y(hy) + (hy >= bounds.y_max ? 25 : -12), '#dfd3be', align, 13);
  }
  text(ctx, 'Robot center', x(row.robot_x), y(0) - run.scenario.safety_distance_m * scale - 13, color, 'center', 13);
  text(ctx, `${run.scenario.safety_distance_m.toFixed(2)} m required`, 25, 23, '#a2b1c0', 'left', 13);
  const sensor = row.sensor_status.replaceAll('_', ' ');
  $(role + '-live').textContent = `${row.speed.toFixed(2)} m/s · Sensor ${sensor} · ${row.action_reason.replaceAll('_', ' ')}`;
}
function chart(pair) {
  const canvas = $('distance-chart'), ctx = canvas.getContext('2d'), W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const runs = [pair.target, pair.hardened], limit = pair.target.scenario.safety_distance_m;
  const ymax = Math.max(2, ...runs.flatMap(r => r.trace.map(row => row.distance_m ?? 0)));
  const scale = W / Math.max(canvas.clientWidth, 200), left = 46 * scale, right = 22 * scale;
  const top = 16 * scale, bottom = 28 * scale;
  const x = t => left + t / pair.target.world.horizon_s * (W - left - right), y = d => H - bottom - d / ymax * (H - top - bottom);
  for (let i = 0; i <= 4; i++) { const d = ymax * i / 4; line(ctx, [[x(0), y(d)], [x(20), y(d)]], '#263541', 1); text(ctx, d.toFixed(1), left - 8 * scale, y(d) + 5 * scale, '#93a5b5', 'right', 13); }
  for (const t of [0, 5, 10, 15, 20]) text(ctx, `${t}s`, x(t), H - 6 * scale, '#93a5b5', 'center', 13);
  line(ctx, [[x(0), y(limit)], [x(20), y(limit)]], '#baff4a', 1.5, true);
  text(ctx, `${limit.toFixed(2)} m`, x(20), y(limit) - 7 * scale, '#baff4a', 'right', 13);
  runs.forEach((run, i) => line(ctx, run.trace.filter(p => p.distance_m != null).map(p => [x(p.t), y(p.distance_m)]), i ? '#65d6ec' : '#ff987e', 2.5));
  line(ctx, [[x(state.time), y(0)], [x(state.time), top]], '#edf3f7', 1);
}
function paint() {
  const pair = state.detail?.pair; if (!pair) return;
  $('clock').textContent = `${state.time.toFixed(2)} s`; $('timeline').value = state.time;
  $('play').textContent = state.playing ? 'Pause' : 'Play'; $('play').setAttribute('aria-label', state.playing ? 'Pause recorded traces' : 'Play recorded traces');
  drawScene($('target-scene'), pair.target, 'target'); drawScene($('hardened-scene'), pair.hardened, 'hardened'); chart(pair);
}
function frame(ts) {
  if (state.playing && state.detail?.pair) {
    if (state.previousFrame != null) state.time = Math.min(state.detail.pair.target.world.horizon_s, state.time + Math.min((ts - state.previousFrame) / 1000, .1) * Number($('speed').value));
    if (state.time >= state.detail.pair.target.world.horizon_s) state.playing = false;
    paint();
  }
  state.previousFrame = ts; requestAnimationFrame(frame);
}
async function startSearch(config) {
  $('start').disabled = true; notice();
  const result = await api('/api/experiments', config);
  state.selected = result.id; await refresh(); return result;
}
$('search-form').addEventListener('submit', event => { event.preventDefault();
  startSearch({case_id: $('case').value, policy: $('policy').value, method: $('method').value, budget: Number($('budget').value)})
    .catch(error => { showError(error); $('start').disabled = !!state.active; }); });
$('case').addEventListener('change', () => { $('case-note').textContent = state.cases.find(c => c.id === $('case').value)?.description || ''; });
$('budget').addEventListener('change', () => { $('simulation-cap').textContent = Number($('budget').value) * 2; });
$('refresh').addEventListener('click', () => refresh().catch(showError));
$('cancel').addEventListener('click', async () => { if (!state.active) return; $('cancel').disabled = true; try { await api(`/api/experiments/${state.active.id}/cancel`, {}); await refresh(); } catch (error) { showError(error); await refresh().catch(showError); } });
for (const id of ['verify', 'journal-verify']) $(id).addEventListener('click', async () => { if (!state.selected) return; $('verify').disabled = true; $('journal-verify').disabled = true; try { await api(`/api/experiments/${state.selected}/verify`, {}); await refresh(); } catch (error) { showError(error); $('verify').disabled = !!state.active; $('journal-verify').disabled = !!state.active; } });
for (const id of ['download', 'journal-download']) $(id).addEventListener('click', event => { if ($(id).getAttribute('aria-disabled') === 'true') { event.preventDefault(); notice('Stop the search before downloading its evidence.'); } });
$('play').addEventListener('click', () => { if (!state.detail?.pair) return; if (state.time >= Number($('timeline').max)) state.time = 0; state.playing = !state.playing; state.previousFrame = null; paint(); });
$('timeline').addEventListener('input', () => { state.playing = false; state.time = Number($('timeline').value); paint(); });
$('jump').addEventListener('click', () => { const violation = state.detail?.pair && firstViolation(state.detail.pair.target); if (violation) { state.playing = false; state.time = violation.time_s; paint(); } });
document.addEventListener('visibilitychange', () => { if (document.hidden) { state.playing = false; paint(); } });
window.addEventListener('resize', paint);
async function init() {
  $('start').disabled = true;
  const data = await api('/api/bootstrap'); state.token = data.csrf_token; state.cases = data.cases; state.list = data;
  $('case').replaceChildren(...data.cases.map(c => { const option = node('option', c.name); option.value = c.id; return option; }));
  $('case').dispatchEvent(new Event('change')); runButtons(data); activity(data);
  const first = data.runs.find(r => r.status !== 'ERROR') || data.recordings[0];
  if (first) await select(first.id);
  if (data.activity) state.timer = setTimeout(() => refresh().catch(showError), 450);
  const context = document.modelContext;
  if (context?.registerTool) {
    const lifecycle = new AbortController();
    window.addEventListener('pagehide', () => lifecycle.abort(), {once: true});
    try {
      await context.registerTool({name: 'read_worldfuzz_experiment', title: 'Read selected experiment',
        description: 'Read the selected WorldFuzz result and separate simulation outcomes without starting work.',
        inputSchema: {type: 'object', properties: {}, additionalProperties: false},
        annotations: {readOnlyHint: true, untrustedContentHint: false}, execute(input) {
          if (input == null || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).length) throw new Error('Expected an empty object');
          const d = state.detail; return d ? {id: d.id, origin: d.origin, status: d.status, counts: d.counts,
            target: d.pair?.target.summary ?? null, hardened: d.pair?.hardened.summary ?? null} : {status: 'NO_SELECTION'};
        }}, {signal: lifecycle.signal});
    } catch { /* Optional browser capability. The visible interface remains available. */ }
  }
}
requestAnimationFrame(frame); init().catch(showError);
