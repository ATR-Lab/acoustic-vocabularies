const byId = id => document.getElementById(id);
let token = '', busy = false;
const pretty = value => String(value).replaceAll('_', ' ');
function render(data) {
  token = data.token || token;
  byId('mode').textContent = data.demo_transport || data.demo ? 'Engineering DEMO' : 'Local station';
  byId('demoNotice').hidden = !data.demo_transport;
  byId('demoTools').hidden = !data.demo_transport;
  const selected = byId('visit').value;
  byId('visit').replaceChildren(...data.choices.map(value => new Option(pretty(value), value)));
  if (data.choices.includes(selected)) byId('visit').value = selected;
  byId('fresh').textContent = 'Updated now';
  for (const id of ['start','pause','resume','stop','checks','deviation','signoff','inject']) byId(id).disabled = !data.loaded;
  if (!data.loaded) return;
  byId('title').textContent = `${data.participant} · ${data.visit}`;
  byId('subtitle').textContent = `Study ${data.study} · Book ${data.book}`;
  byId('state').textContent = pretty(data.state);
  byId('rows').replaceChildren(...data.rows.map(row => {
    const tr = document.createElement('tr');
    for (const value of [pretty(row.block), row.expected, row.actual]) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    return tr;
  }));
  byId('window').textContent = data.window.start ? `Visit window: ${data.window.start} to ${data.window.end}` : 'Initial visit';
  byId('health').replaceChildren(...Object.entries(data.health).map(([key,value]) => {
    const tile = document.createElement('div'), label = document.createElement('span'), reading = document.createElement('strong');
    label.textContent = pretty(key); reading.textContent = typeof value === 'boolean' ? (value ? 'Ready' : 'Unavailable') : `${value.toFixed(1)} ms`;
    if (value === false || (['bridge_age_ms','max_gap_ms'].includes(key) && value > 250)) tile.className = 'bad';
    tile.append(label, reading); return tile;
  }));
  byId('faults').replaceChildren(...(data.faults.length ? data.faults : ['Checks clear']).map(value => {
    const li = document.createElement('li'); li.textContent = pretty(value); return li;
  }));
  byId('deviations').replaceChildren(...data.deviations.map(row => {
    const li = document.createElement('li'); li.textContent = `${row.id} · ${pretty(row.reason)} · ${row.note}`; return li;
  }));
  byId('signed').textContent = data.signoff ? `Signed: ${data.signoff}` : 'Awaiting sign-off';
  byId('start').disabled = !data.can_start || data.state !== 'awaiting_operator';
  byId('resume').disabled = !data.can_start || data.state !== 'paused';
  byId('pause').disabled = data.state !== 'running';
  byId('signoff').disabled = !['complete','stopped'].includes(data.state);
}
function failure(error) {
  byId('error').hidden = false; byId('error').textContent = pretty(error.message || 'connection unavailable');
  byId('fresh').textContent = 'Unavailable';
  byId('start').disabled = byId('resume').disabled = true;
}
async function poll() {
  if (busy) return;
  try {
    const response = await fetch('/api/state', {cache:'no-store'}), data = await response.json();
    if (!response.ok) throw new Error(data.error);
    render(data);
  } catch (error) { failure(error); }
}
async function command(action, payload={}) {
  if (busy) return;
  busy = true;
  try {
    const response = await fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json','X-Console-Token':token},
      body:JSON.stringify({action, staff:byId('staff').value, payload})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    byId('error').hidden = true; render(data);
    if (action === 'deviation') byId('note').value = '';
  } catch (error) { failure(error); }
  finally { busy = false; }
}
byId('load').onclick = () => command('load', {visit:byId('visit').value});
byId('checks').onclick = () => command('checks', {comfort:byId('comfort').checked, phone:byId('phone').checked});
byId('deviation').onclick = () => command('deviation', {reason:byId('reason').value,note:byId('note').value});
byId('inject').onclick = () => command('demo_fault', {fault:byId('fault').value || null});
for (const action of ['start','pause','resume','stop','signoff']) byId(action).onclick = () => command(action);
poll(); setInterval(poll, 500);
