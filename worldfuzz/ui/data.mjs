const $ = id => document.getElementById(id);
let catalog = {episodes: [], datasets: []};
const filters = ['domain', 'status', 'label', 'split'];
function selected() { return catalog.episodes.filter(e => filters.every(k => !$(k).value || e[k] === $(k).value)); }
function render() {
  const rows = selected();
  $('selection').textContent = `${rows.length} matching episodes`;
  $('export').disabled = rows.length === 0;
  $('episodes').replaceChildren();
  for (const episode of rows) {
    const tr = document.createElement('tr');
    for (const key of ['source_key','task','status','label','split']) {
      const td = document.createElement('td'); td.textContent = episode[key]; tr.append(td);
    }
    $('episodes').append(tr);
  }
}
async function refresh() {
  const response = await fetch('/api/data');
  if (!response.ok) throw new Error('Could not read the local catalog.');
  catalog = await response.json();
  const verified = catalog.episodes.filter(e => e.status === 'OUTCOME_VERIFIED').length;
  $('summary').textContent = `${catalog.episodes.length} unique episodes · ${catalog.datasets.length} sources · ${verified} outcomes verified`;
  $('sources').replaceChildren();
  for (const source of catalog.datasets) {
    const p = document.createElement('p');
    p.textContent = `${source.name} · ${source.version} · ${source.license} · ${source.source_url}`;
    $('sources').append(p);
  }
  render();
}
async function run(action) { try { await action(); } catch (e) { $('message').textContent = e.message; } }
filters.forEach(k => $(k).addEventListener('change',render));
$('refresh').addEventListener('click',() => run(refresh));
$('import').addEventListener('click',() => run(async () => {
  $('import').disabled = true;
  try {
    const bootstrap = await (await fetch('/api/bootstrap')).json();
    const response = await fetch('/api/data/import-included', {method:'POST',headers:{'Content-Type':'application/json','X-WorldFuzz-Token':bootstrap.csrf_token},body:'{}'});
    const receipt = await response.json();
    if (!response.ok) throw new Error(receipt.error || 'Import failed.');
    $('message').textContent = `${receipt.added} new episodes imported. Existing episodes were preserved.`;
    await refresh();
  } finally { $('import').disabled = false; }
}));
$('export').addEventListener('click',() => run(async () => {
  const query = new URLSearchParams(filters.filter(k => $(k).value).map(k => [k,$(k).value]));
  const response = await fetch(`/api/data/export?${query}`);
  if (!response.ok) throw new Error('Export failed its filters or integrity check.');
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement('a'); a.href = url; a.download = `worldfuzz-${$('domain').value}.jsonl`; a.click();
  setTimeout(() => URL.revokeObjectURL(url),1000);
}));
run(refresh);
