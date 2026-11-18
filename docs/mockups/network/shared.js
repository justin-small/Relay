/* Panes every option shares: This computer (today's Network page), Sources, and the device table. */
(function () {
  'use strict';
  var N = window.NET, h = N.h;

  function thisComputer(root) {
    root.textContent = '';
    var st = h('div', 'stack', root);
    var w = h('div', 'what', st), wd = h('div', '', w); h('b', '', wd, 'This computer’s network ports, and which one each kind of Relay traffic uses');
    h('p', '', wd, 'Traffic with no port chosen uses the system’s default network. A change applies at once; one that would take the panel off the address you are using is refused.');
    var c1 = h('div', 'card', st); h('h3', '', c1, 'Ports');
    var t = h('table', 'dt nettable', h('div', 'tablescroll', c1)); var hr = h('tr', '', h('thead', '', t)); ['Port', 'Address', 'Link', 'Speed', 'Traffic on it', ''].forEach(function (x) { h('th', '', hr, x); });
    var tb = h('tbody', '', t);
    N.PORTS.forEach(function (p) { var r = h('tr', '', tb); var n = h('td', '', r); h('b', '', n, p.name); h('div', 'muted mono', n, p.id); h('td', 'mono', r, p.addr); var l = h('td', '', r); h('span', 'badge ok', l, p.link); h('td', '', r, p.speed); var tr = h('td', '', r); var g = h('div', 'gap', tr); p.traffic.forEach(function (x) { h('span', 'tag', g, x); }); h('td', '', r); });
    h('p', 'hint', c1, 'Updates by itself when a cable is pulled or an adapter plugged in.');
    var c2 = h('div', 'card', st); h('h3', '', c2, 'Traffic');
    var t2 = h('table', 'dt nettable', h('div', 'tablescroll', c2)); var hr2 = h('tr', '', h('thead', '', t2)); ['Traffic', 'From', 'Network port', 'If the port is missing', 'Status', ''].forEach(function (x) { h('th', '', hr2, x); });
    var tb2 = h('tbody', '', t2);
    N.TRAFFIC.forEach(function (x) {
      var r = h('tr', '', tb2); h('td', '', r, x[0]); h('td', 'muted', r, x[1]);
      var sel = h('select', '', h('td', '', r)); h('option', '', sel, 'System default').value = '';
      N.PORTS.forEach(function (p) { var o = h('option', '', sel, p.name + ' (' + p.id + ')'); o.value = p.id; if (p.id === x[2]) o.selected = true; });
      var pol = h('select', '', h('td', '', r)); [['fallback', 'Use the default network'], ['strict', 'Stop it']].forEach(function (o) { var e = h('option', '', pol, o[1]); e.value = o[0]; if (o[0] === x[3]) e.selected = true; });
      var stt = h('td', '', r); if (x[2]) h('span', 'badge ok', stt, 'On ' + x[2]); else h('span', 'badge warn', stt, 'Default network');
      var b = h('button', 'sm', h('td', '', r), 'Test'); b.type = 'button';
    });
  }

  function sources(root) {
    root.textContent = '';
    var st = h('div', 'stack', root);
    var w = h('div', 'what', st), wd = h('div', '', w); h('b', '', wd, 'Where the map comes from');
    h('p', '', wd, 'Relay merges what each source sees: a device seen by two sources is drawn once. Modules add more; turning one off removes only what it added.');
    var c = h('div', 'card', st);
    N.SOURCES.forEach(function (s) {
      var r = h('div', 'srcrow', c); h('i', 'dot ' + (s.off ? '' : 'ok'), r);
      var n = h('div', '', r); h('b', '', n, s.label); h('small', '', n, s.adds);
      h('span', 'muted', r, s.off ? 'Off' : s.count + ' devices · ' + s.age);
      var a = h('a', '', r, s.kind === 'core' ? 'Built in' : s.off ? 'Turn on in Modules' : 'Module settings'); a.href = '#';
      if (s.kind === 'core') a.removeAttribute('href');
    });
    var b = h('div', 'ctl', st); var rb = h('button', '', b, 'Look again now'); rb.type = 'button'; h('span', 'muted', b, 'Relay looks every 30 seconds by itself.');
  }

  /* A name that reads as text; clicking it turns it into a text box. Enter or leaving keeps it, Escape puts it back. */
  var RENAMED = { cam3: 'Wide (back wall)' };
  function nameCell(td, d) {
    td.textContent = '';
    var cur = RENAMED[d.id] || d.name;
    var b = h('button', 'namebtn', td, (d.warn ? '⚠ ' : '') + cur); b.type = 'button'; b.title = 'Click to rename'; b.setAttribute('aria-label', 'Rename ' + cur);
    if (RENAMED[d.id]) h('small', 'hint', td, 'Device calls itself ' + d.name);
    if (d.off) { td.appendChild(document.createTextNode(' ')); h('span', 'badge err', td, 'Offline'); }
    b.addEventListener('click', function (e) {
      e.stopPropagation();
      td.textContent = '';
      var inp = h('input', 'nameedit', td); inp.value = cur; inp.setAttribute('aria-label', 'Name for ' + d.name); inp.placeholder = d.name;
      var done = false;
      function finish(keep) {
        if (done) return; done = true;
        var v = inp.value.trim();
        if (keep) { if (!v || v === d.name) delete RENAMED[d.id]; else RENAMED[d.id] = v; }
        nameCell(td, d);
      }
      inp.addEventListener('keydown', function (k) { if (k.key === 'Enter') finish(true); if (k.key === 'Escape') finish(false); });
      inp.addEventListener('blur', function () { finish(true); });
      inp.focus(); inp.select();
    });
  }

  /* the device table; opts: {notes: bool, onPick} */
  function deviceTable(root, opts) {
    opts = opts || {};
    root.textContent = '';
    var bar = h('div', 'toolbar', root); bar.style.marginBottom = 'var(--sp-3)';
    var q = h('input', '', bar); q.type = 'search'; q.placeholder = 'Find name, IP or MAC'; q.setAttribute('aria-label', 'Find name, IP or MAC');
    var sg = h('div', 'seg', bar); [['all', 'All'], ['av', 'AV only'], ['ndi', 'NDI'], ['dante', 'Dante'], ['check', 'Needs a look']].forEach(function (x, i) { var b = h('button', i ? '' : 'on', sg, x[1]); b.type = 'button'; b.dataset.v = x[0]; });
    h('span', 'grow', bar);
    var sw = h('select', '', bar); sw.setAttribute('aria-label', 'Switch'); h('option', '', sw, 'Every switch').value = '';
    N.GEAR.forEach(function (g) { if (g.ports) { var o = h('option', '', sw, g.name); o.value = g.id; } });
    var t = h('table', 'dt', h('div', 'tablescroll', root)); var hr = h('tr', '', h('thead', '', t));
    [opts.notes ? 'Name in Relay' : 'Name', 'What', 'Address', 'Plugged into', 'Link', 'PoE', 'Carries'].concat(opts.notes ? ['Note'] : ['Seen by']).forEach(function (x) { h('th', '', hr, x); });
    var tb = h('tbody', '', t), filter = 'all';
    function draw() {
      tb.textContent = ''; var qq = q.value.toLowerCase();
      N.DEV.forEach(function (d) {
        if (filter === 'av' && !(d.ndi || d.dante)) return; if (filter === 'ndi' && !d.ndi) return; if (filter === 'dante' && !d.dante) return;
        if (filter === 'check' && !(d.warn || d.off)) return; if (sw.value && d.sw !== sw.value) return;
        if (qq && (d.name + ' ' + d.ip + ' ' + d.mac).toLowerCase().indexOf(qq) < 0) return;
        var r = h('tr', '', tb); r.dataset.id = d.id;
        var n = h('td', '', r);
        if (opts.notes) {
          nameCell(n, d);
        } else { h('b', '', n, (d.warn ? '⚠ ' : '') + d.name); if (d.off) { n.appendChild(document.createTextNode(' ')); h('span', 'badge err', n, 'Offline'); } }
        h('td', 'muted', r, d.what); h('td', 'mono', r, d.ip);
        h('td', '', r, d.sw ? N.gear(d.sw).name + ' · ' + d.port : '—');
        var l = h('td', '', r, N.speed(d.speed)); if (d.warn) l.style.color = 'var(--warn)';
        h('td', '', r, d.poe ? d.poe + ' W' : '');
        var cr = h('td', '', r); if (d.ndi) h('span', 'tag ndi', cr, 'NDI'); if (d.dante) h('span', 'tag dante', cr, 'Dante');
        if (opts.notes) { var inp = h('input', '', h('td', '', r)); inp.value = d.sw ? N.NOTES[d.sw + ':' + d.port] || '' : ''; inp.placeholder = 'Add a note'; inp.setAttribute('aria-label', 'Note for ' + d.name); }
        else h('td', 'muted', r, d.seen.join(', '));
        r.addEventListener('click', function (e) { if (e.target.tagName === 'INPUT') return; tb.querySelectorAll('tr').forEach(function (x) { x.classList.toggle('sel', x === r); }); if (opts.onPick) opts.onPick(d.id); });
      });
    }
    N.seg(sg, function (v) { filter = v; draw(); }); q.addEventListener('input', draw); sw.addEventListener('change', draw); draw();
    if (opts.notes) { var b = h('div', 'ctl', root); b.style.marginTop = 'var(--sp-3)'; var sv = h('button', 'primary', b, 'Save names and notes'); sv.type = 'button'; h('span', 'muted', b, 'Names and notes are kept with the device’s MAC, so they survive a new address. A name changes only what Relay shows; nothing is sent to the device or UniFi.'); }
  }

  window.NETUI = { thisComputer: thisComputer, sources: sources, deviceTable: deviceTable };
})();
