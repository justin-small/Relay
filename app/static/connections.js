/* The Connections page (#629): the serial, Modbus and MQTT connections modules use. Add one (a serial server on the
   network, a local serial port, a Modbus device, an MQTT broker), press Test to see it answer, edit it, remove it.
   Loaded after admin.js; uses what it shares (window.RelayAdmin) and the shared helpers (window.relayUI). Everything
   from the server is set as text. An MQTT password is never sent back: the form says whether one is saved. */
(function () {
  const A = window.RelayAdmin;
  const U = window.relayUI;
  const g = function (id) { return document.getElementById(id); };
  if (!A || !U || !g('page-connections')) return;
  const el = U.el;
  const manage = g('page-connections').getAttribute('data-manage') === 'yes';
  const KINDS = { serial: 'Serial', modbus: 'Modbus', mqtt: 'MQTT' };
  const LINKS = { tcp: 'Serial over TCP', udp: 'Serial over UDP', serial: 'Local serial port' };
  const BAUDS = [1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800, 115200, 230400];
  const PARITY = ['none', 'even', 'odd', 'mark', 'space'];
  const FLOW = { none: 'None', rtscts: 'RTS/CTS', xonxoff: 'XON/XOFF' };
  const TERMINATORS = { cr: 'CR', lf: 'LF', crlf: 'CR LF', nul: 'NUL' };
  let data = { connections: [], serial_ports: [], traffic: [], kinds: [] };
  let editing = null;      // the id being edited, or null for a new one
  let draft = null;        // what the form holds
  let hasPassword = false; // an MQTT password is already saved for the connection being edited
  let probe = { send: '', terminator: 'cr', address: '0' };
  let timer = 0;

  function blank() {
    return { name: '', kind: 'serial', traffic: 'connections',
      link: { kind: 'tcp', host: '', port: '4196', device: '', baud: '19200', databits: '8', parity: 'none', stopbits: '1', flow: 'none' },
      modbus: { framing: 'tcp', unit: '1', timeout: '1', retries: '1' },
      mqtt: { host: '', port: '', tls: false, username: '', password: '', client_id: '', keepalive: '60', ca: '',
        will: { topic: '', payload: '', qos: '0', retain: false } } };
  }

  function from(conn) {
    const d = blank();
    d.name = conn.name; d.kind = conn.kind; d.traffic = conn.traffic;
    const s = conn.settings;
    if (s.link) Object.keys(s.link).forEach(function (k) { d.link[k] = String(s.link[k]); });
    if (s.modbus) Object.keys(s.modbus).forEach(function (k) { d.modbus[k] = String(s.modbus[k]); });
    if (s.mqtt) {
      Object.keys(d.mqtt).forEach(function (k) {
        if (k === 'will' || k === 'password' || s.mqtt[k] === undefined) return;
        d.mqtt[k] = typeof d.mqtt[k] === 'boolean' ? !!s.mqtt[k] : String(s.mqtt[k]);
      });
      if (s.mqtt.will) d.mqtt.will = { topic: s.mqtt.will.topic, payload: s.mqtt.will.payload, qos: String(s.mqtt.will.qos), retain: !!s.mqtt.will.retain };
      hasPassword = !!s.mqtt.has_password;
    }
    return d;
  }

  function where(c) {
    const s = c.settings;
    if (c.kind === 'mqtt') return s.mqtt.host + ':' + s.mqtt.port + (s.mqtt.tls ? ' (TLS)' : '');
    const l = s.link;
    if (l.kind === 'serial') return l.device + ' at ' + l.baud + ' ' + l.databits + l.parity.charAt(0).toUpperCase() + l.stopbits;
    return (l.kind === 'udp' ? 'UDP ' : '') + l.host + ':' + l.port;
  }

  function status(c) {
    const td = el('td', '');
    if (!c.state) { td.appendChild(el('span', 'muted', 'Not in use')); return td; }
    td.appendChild(U.pill(c.state.connected ? 'Connected' : (c.state.last_error || 'Not connected'), c.state.connected ? 'on' : 'err'));
    return td;
  }

  function row(c) {
    const tr = el('tr', '');
    tr.appendChild(el('td', '', c.name));
    tr.appendChild(el('td', '', KINDS[c.kind] || c.kind));
    tr.appendChild(el('td', 'mono', where(c)));
    tr.appendChild(status(c));
    const act = el('td', '');
    if (manage) {
      act.appendChild(U.button('Edit', function () { open(c); }, 'sm'));
      act.appendChild(U.button('Remove', async function () {
        if (!(await U.confirm('Remove ' + c.name + '? Anything using it stops being able to reach it.', { danger: true }))) return;
        try { await A.api('/api/admin/connections/' + c.id, { method: 'DELETE' }); if (editing === c.id) close(); load(); }
        catch (e) { A.alertMsg(e.message); }
      }, 'sm danger'));
    }
    tr.appendChild(act);
    return tr;
  }

  function paint() {
    const tb = g('connList');
    tb.textContent = '';
    data.connections.forEach(function (c) { tb.appendChild(row(c)); });
    if (!data.connections.length) {
      const tr = el('tr', '');
      const td = el('td', 'muted', 'No connection yet.');
      td.colSpan = 5;
      tr.appendChild(td);
      tb.appendChild(tr);
    }
  }

  async function load() {
    try {
      data = await A.api('/api/admin/connections');
      paint();
    } catch (e) {
      A.alertMsg(e.message);
    }
  }

  // ---- the form

  function text(label, obj, key, opts) {
    const o = opts || {};
    const input = el('input', { type: o.type || 'text', value: obj[key] });
    if (o.placeholder) input.placeholder = o.placeholder;
    if (o.list) input.setAttribute('list', o.list);
    input.addEventListener('input', function () { obj[key] = input.value; });
    return U.field(label, input, 'conn_' + (o.id || key));
  }

  function choose(label, obj, key, options, redraw, id) {
    const sel = el('select', '');
    options.forEach(function (o) {
      const opt = el('option', { value: o[0], text: o[1] });
      if (String(obj[key]) === String(o[0])) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener('change', function () { obj[key] = sel.value; if (redraw) draw(); });
    return U.field(label, sel, 'conn_' + (id || key));
  }

  function tick(label, obj, key) {
    const box = el('input', { type: 'checkbox' });
    box.checked = !!obj[key];
    box.addEventListener('change', function () { obj[key] = box.checked; });
    return el('label', 'check', [box, ' ' + label]);
  }

  function linkFields(host) {
    const l = draft.link;
    host.appendChild(choose('How it is connected', l, 'kind', Object.keys(LINKS).map(function (k) { return [k, LINKS[k]]; }), true, 'linkkind'));
    if (l.kind === 'serial') {
      const ports = el('datalist', { id: 'conn_ports' });
      data.serial_ports.forEach(function (p) { ports.appendChild(el('option', { value: p.device, text: p.description })); });
      host.appendChild(ports);
      host.appendChild(text('Serial port', l, 'device', { list: 'conn_ports', id: 'device', placeholder: '/dev/ttyUSB0 or COM3' }));
      host.appendChild(choose('Baud rate', l, 'baud', BAUDS.map(function (b) { return [b, String(b)]; }), false, 'baud'));
      host.appendChild(choose('Data bits', l, 'databits', [5, 6, 7, 8].map(function (b) { return [b, String(b)]; }), false, 'databits'));
      host.appendChild(choose('Parity', l, 'parity', PARITY.map(function (p) { return [p, p.charAt(0).toUpperCase() + p.slice(1)]; }), false, 'parity'));
      host.appendChild(choose('Stop bits', l, 'stopbits', [[1, '1'], [2, '2']], false, 'stopbits'));
      host.appendChild(choose('Flow control', l, 'flow', Object.keys(FLOW).map(function (k) { return [k, FLOW[k]]; }), false, 'flow'));
    } else {
      host.appendChild(text('Address of the serial server', l, 'host', { id: 'host', placeholder: '10.10.0.181' }));
      host.appendChild(text('Port', l, 'port', { id: 'port', type: 'number' }));
      host.appendChild(el('p', 'sub', 'Set the serial speed and framing on the serial server itself, to match the device.'));
    }
  }

  function modbusFields(host) {
    const m = draft.modbus;
    host.appendChild(choose('Modbus framing', m, 'framing', [['tcp', 'Modbus TCP'], ['rtu', 'Modbus RTU']], false, 'framing'));
    host.appendChild(text('Unit id', m, 'unit', { id: 'unit', type: 'number' }));
    host.appendChild(text('Timeout (seconds)', m, 'timeout', { id: 'timeout', type: 'number' }));
    host.appendChild(text('Retries', m, 'retries', { id: 'retries', type: 'number' }));
  }

  function mqttFields(host) {
    const m = draft.mqtt;
    host.appendChild(text('Broker address', m, 'host', { id: 'mhost', placeholder: 'broker.local' }));
    host.appendChild(text('Port (blank for the usual one)', m, 'port', { id: 'mport', type: 'number' }));
    host.appendChild(tick('Use TLS', m, 'tls'));
    host.appendChild(text('User name', m, 'username', { id: 'username' }));
    const pw = text('Password', m, 'password', { id: 'password', type: 'password',
      placeholder: hasPassword ? 'A password is saved. Leave blank to keep it.' : '' });
    host.appendChild(pw);
    host.appendChild(text('Client id (blank for one Relay makes)', m, 'client_id', { id: 'client_id' }));
    host.appendChild(text('Keep-alive (seconds)', m, 'keepalive', { id: 'keepalive', type: 'number' }));
    const ca = el('textarea', { rows: 4, value: m.ca });
    ca.addEventListener('input', function () { m.ca = ca.value; });
    host.appendChild(U.field("Broker's certificate (only for a broker with its own, in PEM form)", ca, 'conn_ca'));
    host.appendChild(el('h4', '', 'Last will'));
    host.appendChild(text('Topic (blank for none)', m.will, 'topic', { id: 'will_topic' }));
    host.appendChild(text('Message', m.will, 'payload', { id: 'will_payload' }));
    host.appendChild(choose('QoS', m.will, 'qos', [[0, '0'], [1, '1']], false, 'will_qos'));
    host.appendChild(tick('Retain', m.will, 'retain'));
  }

  function probeFields(host) {
    if (draft.kind === 'serial') {
      host.appendChild(el('h4', '', 'Test by sending'));
      host.appendChild(text('Send (blank to only connect)', probe, 'send', { id: 'probe_send', placeholder: '?ID 0' }));
      host.appendChild(choose('End it with', probe, 'terminator', Object.keys(TERMINATORS).map(function (k) { return [k, TERMINATORS[k]]; }), false, 'probe_term'));
    } else if (draft.kind === 'modbus') {
      host.appendChild(el('h4', '', 'Test by reading'));
      host.appendChild(text('Holding register to read', probe, 'address', { id: 'probe_address', type: 'number' }));
    }
  }

  function draw() {
    const host = g('connFields');
    host.textContent = '';
    host.appendChild(text('Name', draft, 'name', { id: 'name' }));
    host.appendChild(choose('Kind', draft, 'kind', Object.keys(KINDS).map(function (k) { return [k, KINDS[k]]; }), true, 'kind'));
    if (draft.kind === 'mqtt') mqttFields(host);
    else { linkFields(host); if (draft.kind === 'modbus') modbusFields(host); }
    host.appendChild(choose('Network traffic', draft, 'traffic', data.traffic.map(function (t) { return [t, t]; }), false, 'traffic'));
    probeFields(host);
  }

  function payload() {
    const d = JSON.parse(JSON.stringify(draft));
    const out = { name: d.name, kind: d.kind, traffic: d.traffic };
    if (d.kind === 'mqtt') {
      out.mqtt = d.mqtt;
      if (!out.mqtt.password && editing !== null) delete out.mqtt.password;
      if (!out.mqtt.will.topic) delete out.mqtt.will;
    } else {
      out.link = d.link;
      if (d.kind === 'modbus') out.modbus = d.modbus;
    }
    return out;
  }

  function open(conn) {
    editing = conn ? conn.id : null;
    hasPassword = false;
    draft = conn ? from(conn) : blank();
    g('connFormTitle').textContent = conn ? 'Edit ' + conn.name : 'Add a connection';
    g('connResult').textContent = '';
    g('connForm').hidden = false;
    draw();
    g('connForm').scrollIntoView({ block: 'nearest' });
  }

  function close() {
    editing = null;
    draft = null;
    g('connForm').hidden = true;
  }

  function show(res) {
    const box = g('connResult');
    box.textContent = '';
    box.appendChild(U.pill(res.ok ? 'Works' : 'Failed', res.ok ? 'on' : 'err'));
    box.appendChild(el('span', '', ' ' + res.detail));
    if (res.reply !== undefined) box.appendChild(el('p', 'mono', res.reply + '   (' + res.hex + ')'));
  }

  const note = U.note('connNote');
  g('connTestBtn').addEventListener('click', async function () {
    const btn = g('connTestBtn');
    btn.disabled = true;
    note('');
    try {
      const body = { connection: payload(), probe: probe };
      if (editing !== null) body.id = editing;
      show(await A.post('/api/admin/connections/test', body));
    } catch (e) { note(e.message); } finally { btn.disabled = false; }
  });
  g('connSaveBtn').addEventListener('click', async function () {
    const btn = g('connSaveBtn');
    btn.disabled = true;
    note('');
    try {
      await A.post('/api/admin/connections' + (editing === null ? '' : '/' + editing), payload());
      close();
      load();
    } catch (e) { note(e.message); } finally { btn.disabled = false; }
  });
  g('connCancelBtn').addEventListener('click', close);
  const add = g('connAddBtn');
  if (!manage) { add.hidden = true; ['connTestBtn', 'connSaveBtn'].forEach(function (id) { g(id).hidden = true; }); }
  add.addEventListener('click', function () { open(null); });

  window.addEventListener('hashchange', function () { if (location.hash === '#connections') load(); });
  if (location.hash === '#connections') load();
  timer = setInterval(function () { if (location.hash === '#connections') load(); }, 5000);
})();
