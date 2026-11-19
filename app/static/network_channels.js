/* The Network page's Channels section (#542): every audio, video and talk channel, however many modules report it. A module
   reports its own ends; Relay suggests which are one channel and an operator joins them, says they differ, renames, adds a
   note, splits an end back out, or forgets a channel nothing reports. Everything from the server is set as text. */
(function (root) {
  'use strict';
  var doc = root.document;
  if (!doc || !doc.getElementById('nmChannels')) return;
  var $ = function (id) { return doc.getElementById(id); };
  var el = root.relayUI.el;
  var send = root.relayUI.api;
  var canManage = root.relayUI.can('network.manage');
  var KIND = { audio: 'Audio', video: 'Video', talk: 'Talk' };
  var WHY = { name: 'the same name', number: 'the same number on the same gear' };
  var view = null;

  var fail = root.relayUI.note('nmChanErr');
  function change(method, path, body) {
    fail('');
    return send(method, path, body).then(function (v) { view = v; paint(); }).catch(function (e) { fail(e.message); });
  }

  function endText(e) {
    return e.provider + (e.number != null ? ' ' + e.number : '') + ': ' + (e.gear_name || e.key) + (e.missing ? ' (not reported now)' : '');
  }

  function paintSuggestions() {
    var box = $('nmChanSuggest');
    box.textContent = '';
    ((view && view.suggestions) || []).forEach(function (s) {
      var row = el('div', 'nmsuggest');
      row.appendChild(el('span', '', s.a.shown + ' (' + s.a.providers.join(', ') + ') and ' + s.b.shown + ' (' +
        s.b.providers.join(', ') + ') have ' + WHY[s.why] + '.'));
      if (canManage) {
        row.appendChild(root.relayUI.button('Join as one', function () {
          return change('POST', '/api/admin/network/channels/merge', { keep: s.a.ref, other: s.b.ref });
        }, 'primary sm'));
        row.appendChild(root.relayUI.button('Not the same', function () {
          return change('POST', '/api/admin/network/channels/decline', { a: s.a.ref, b: s.b.ref });
        }, 'quiet sm'));
      }
      box.appendChild(row);
    });
  }

  function paintRow(c) {
    var tr = el('tr');
    var nm = el('td'), name = el('input', 'nmnote');
    name.value = c.name; name.placeholder = c.shown; name.maxLength = 60; name.disabled = !canManage;
    name.setAttribute('aria-label', 'Name for the channel ' + c.shown);
    name.addEventListener('change', function () {
      change('POST', '/api/admin/network/channels/' + c.id + '/rename', { name: name.value.trim() });
    });
    nm.appendChild(name);
    if (c.name && c.gear_name && c.gear_name !== c.name) nm.appendChild(el('div', 'muted', 'Gear calls it ' + c.gear_name));
    if (canManage && c.can_device) {
      nm.appendChild(root.relayUI.button('Rename on the device', function () {
        var text = (name.value || c.shown).trim();
        return change('POST', '/api/admin/network/channels/' + c.id + '/rename', { name: text, device: true });
      }, 'quiet sm'));
      nm.appendChild(el('div', 'muted', 'Typing a name changes Relay only.'));
    } else if (canManage) {
      nm.appendChild(el('div', 'muted', 'Relay only: this gear is not renamed from here.'));
    }
    tr.appendChild(nm);
    tr.appendChild(el('td', '', KIND[c.kind] || c.kind));
    var ends = el('td', 'muted');
    c.ends.forEach(function (e) {
      var line = el('div', '', endText(e));
      if (canManage && c.ends.length > 1) {
        line.appendChild(root.relayUI.button('Split off', function () {
          return change('POST', '/api/admin/network/channels/split', { end: e.id });
        }, 'quiet sm'));
      }
      ends.appendChild(line);
    });
    tr.appendChild(ends);
    var nt = el('td'), note = el('input', 'nmnote');
    note.value = c.note || ''; note.placeholder = 'Add a note'; note.maxLength = 300; note.disabled = !canManage;
    note.setAttribute('aria-label', 'Note for the channel ' + c.shown);
    note.addEventListener('change', function () {
      change('POST', '/api/admin/network/channels/' + c.id + '/note', { text: note.value.trim() });
    });
    nt.appendChild(note); tr.appendChild(nt);
    var act = el('td');
    if (canManage && c.missing) {
      act.appendChild(root.relayUI.button('Forget', function () {
        return change('DELETE', '/api/admin/network/channels/' + c.id);
      }, 'quiet sm'));
    }
    tr.appendChild(act);
    return tr;
  }

  function paint() {
    paintSuggestions();
    var body = $('nmChanRows');
    body.textContent = '';
    ((view && view.channels) || []).forEach(function (c) { body.appendChild(paintRow(c)); });
    if (!body.children.length) {
      var r = el('tr'), td = el('td', 'muted', 'No channels reported yet. NDI, Blackmagic, OBS, the audio bus and the intercom add theirs.');
      td.colSpan = 5; r.appendChild(td); body.appendChild(r);
    }
  }

  function load() {
    send('GET', '/api/admin/network/channels', undefined, { cache: 'no-store' })
      .then(function (v) { view = v; paint(); })
      .catch(function () { fail('Channels could not be read. Open the page again to retry.'); });
  }

  $('page-network').addEventListener('relay:tab', load);   // the page opened or changed tab (admin.js showPage)
  if (root.relayUI.shown($('nmChannels'))) load();
})(window);
