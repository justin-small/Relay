/* The Signage admin page (#205): screens and what each plays now, screen groups, playlists and their items, the
   media library with uploads, and the screen port. Loaded by admin.html only while the module is on.
   Thumbnails are made here, in the browser, when a file is uploaded: Relay has no image or video library. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-signage')) return;
  const $ = R.$, api = R.api, post = R.post, el = R.el;
  const can = relayUI.can;

  const page = document.getElementById('page-signage');
  let data = null;
  let tab = 'screens';
  let editing = null;          // screen id being edited, 0 for a new one, null when the form is closed
  let current = null;          // playlist id open in the editor
  let draft = null;            // that playlist's items as edited, not yet saved
  let dirty = false;
  let source = 'media';
  let player = null;
  let timer = null;

  function showError(text) { $('sgErr').textContent = text || ''; $('sgErr').hidden = !text; }
  async function call(fn) {
    showError('');
    try { return await fn(); } catch (e) { showError(e.message); return null; }
  }
  function send(method, path, payload) {
    return api(path, { method: method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}) });
  }
  function size(n) {
    if (n < 0) return 'unknown';
    if (n === 0) return '0 MB';
    if (n < 1024 * 1024) return Math.max(1, Math.round(n / 1024)) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + ' MB';
    return (n / 1073741824).toFixed(1) + ' GB';
  }
  function thumb(src, label) {
    if (!src) { const d = el('div', 'sg-thumb ph', label || ''); return d; }
    const i = el('img', 'sg-thumb'); i.alt = ''; i.loading = 'lazy'; i.src = src; return i;
  }
  function option(sel, value, text, selected) {
    const o = el('option', '', text); o.value = value; if (selected) o.selected = true; sel.appendChild(o);
  }
  function playlistSelect(sel, value, none) {
    sel.textContent = '';
    option(sel, '', none, !value);
    for (const p of data.playlists) option(sel, p.id, p.name, p.id === value);
  }
  function button(text, cls, onclick) {
    const b = el('button', cls || '', text); b.type = 'button'; b.onclick = onclick; return b;
  }

  /* ---------------------------------------------------------------- tabs */
  function setTab(name) {
    tab = name;
    for (const b of page.querySelectorAll('[data-sgtab]')) b.setAttribute('aria-selected', String(b.dataset.sgtab === name));
    for (const p of page.querySelectorAll('[data-sgpane]')) p.hidden = p.dataset.sgpane !== name;
  }
  for (const b of page.querySelectorAll('[data-sgtab]')) b.onclick = function () { setTab(b.dataset.sgtab); };

  /* ---------------------------------------------------------------- render */
  function render() {
    const manage = can('signage.manage');
    for (const n of page.querySelectorAll('.sg-manage')) n.hidden = !manage;
    $('sgAccess').hidden = manage;
    const port = data.port;
    $('sgRestart').hidden = !port.restart;
    $('sgRestart').textContent = port.bound
      ? 'Screens are on port ' + port.bound + '. Restart Relay to move them to port ' + port.configured + '.'
      : 'Restart Relay to open port ' + port.configured + ' for the screens. Until then they cannot be reached.';
    $('sgSum').textContent = data.screens.length ? data.screens.length + (data.screens.length === 1 ? ' screen' : ' screens') : '';
    renderScreens();
    renderGroups();
    renderPlaylists();
    renderSchedule();
    renderTakeover();
    renderMedia();
    if (document.activeElement !== $('sgPort')) $('sgPort').value = data.settings.port;
    if (document.activeElement !== $('sgZone')) {
      $('sgZone').textContent = '';
      for (const z of data.timezones) option($('sgZone'), z.id, z.label, z.id === data.settings.timezone);
      $('sgOvUntil').value = data.settings.override_until;
    }
    $('sgPortState').textContent = port.bound ? 'Serving screens on port ' + port.bound + ' now.' : '';
  }

  function renderScreens() {
    const body = $('sgScreens');
    body.textContent = '';
    $('sgNoScreens').hidden = data.screens.length > 0;
    for (const s of data.screens) {
      const tr = el('tr');
      const now = el('td');
      if (s.now && s.now.seen_s < 90) {
        now.appendChild(thumb(s.now.thumb, s.now.label));
        now.appendChild(el('div', 'muted', s.now.label || ''));
      } else {
        now.appendChild(thumb('', 'Offline'));
        now.appendChild(el('div', 'muted', s.now ? 'Last seen ' + R.agoText(s.now.seen_s) : 'Not opened yet'));
      }
      tr.appendChild(now);
      const name = el('td');
      name.appendChild(el('div', '', s.name));
      if (s.group_name) name.appendChild(el('div', 'muted', 'Group: ' + s.group_name));
      if (s.sound) name.appendChild(el('div', 'muted', 'Sound on'));
      tr.appendChild(name);
      const pl = s.playing_playlist;
      const play = el('td', pl ? '' : 'muted', pl ? pl.name : 'Nothing assigned');
      if (s.taken_over) play.appendChild(el('div', '', 'Taken over: ' + s.taken_over.label +
        (s.taken_over.until ? ' until ' + new Date(s.taken_over.until).toLocaleTimeString() : '')));
      if (s.banner) play.appendChild(el('div', '', 'Alert banner showing'));
      if (pl && pl.why) play.appendChild(el('div', 'muted', pl.why + (pl.until ? ' until ' + new Date(pl.until).toLocaleString() : '')));
      tr.appendChild(play);
      const addr = el('td');
      for (const u of s.urls.slice(0, 2)) {
        const a = el('a', 'mono', u); a.href = u; a.target = '_blank'; a.rel = 'noopener';
        addr.appendChild(a); addr.appendChild(el('br'));
      }
      tr.appendChild(addr);
      const act = el('td');
      act.appendChild(button('Preview', '', function () { openPreview({ screen: s.slug }, s.name); }));
      if (can('signage.manage')) {
        act.appendChild(button('Edit', '', function () { openScreenForm(s); }));
        act.appendChild(button('Delete', 'danger', async function () {
          if (!(await relayUI.confirm('Delete the screen "' + s.name + '"? Its address stops working.'))) return;
          call(async function () { await send('DELETE', '/api/admin/signage/screens/' + s.id); await refresh(); });
        }));
      }
      tr.appendChild(act);
      body.appendChild(tr);
    }
  }

  function openScreenForm(s) {
    editing = s ? s.id : 0;
    $('sgScreenForm').hidden = false;
    $('sgScreenName').value = s ? s.name : '';
    $('sgScreenSlug').value = s ? s.slug : '';
    $('sgScreenSlug').dataset.auto = s ? '' : '1';
    $('sgScreenSound').checked = !!(s && s.sound);
    const g = $('sgScreenGroup');
    g.textContent = '';
    option(g, '', 'No group', !(s && s.group_id));
    for (const x of data.groups) option(g, x.id, x.name, s && s.group_id === x.id);
    playlistSelect($('sgScreenPlaylist'), s ? s.playlist_id : null, 'None');
    $('sgScreenName').focus();
  }
  $('sgScreenAdd').onclick = function () { openScreenForm(null); };
  $('sgScreenCancel').onclick = function () { editing = null; $('sgScreenForm').hidden = true; };
  $('sgScreenName').oninput = function () {
    if ($('sgScreenSlug').dataset.auto) {
      $('sgScreenSlug').value = $('sgScreenName').value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);
    }
  };
  $('sgScreenSlug').oninput = function () { $('sgScreenSlug').dataset.auto = ''; };
  $('sgScreenForm').onsubmit = function (ev) {
    ev.preventDefault();
    const payload = {
      name: $('sgScreenName').value, slug: $('sgScreenSlug').value, sound: $('sgScreenSound').checked,
      group_id: $('sgScreenGroup').value ? Number($('sgScreenGroup').value) : null,
      playlist_id: $('sgScreenPlaylist').value ? Number($('sgScreenPlaylist').value) : null
    };
    call(async function () {
      if (editing) await send('PUT', '/api/admin/signage/screens/' + editing, payload);
      else await post('/api/admin/signage/screens', payload);
      editing = null; $('sgScreenForm').hidden = true;
      await refresh();
    });
  };

  function renderGroups() {
    const body = $('sgGroups');
    body.textContent = '';
    $('sgNoGroups').hidden = data.groups.length > 0;
    for (const g of data.groups) {
      const tr = el('tr');
      tr.appendChild(el('td', '', g.name));
      const td = el('td');
      const sel = el('select');
      playlistSelect(sel, g.playlist_id, 'None (each screen plays its own)');
      sel.disabled = !can('signage.manage');
      sel.onchange = function () {
        call(async function () {
          await send('PUT', '/api/admin/signage/groups/' + g.id, { name: g.name, playlist_id: sel.value ? Number(sel.value) : null });
          await refresh();
        });
      };
      td.appendChild(sel);
      tr.appendChild(td);
      const members = data.screens.filter(function (s) { return s.group_id === g.id; }).map(function (s) { return s.name; });
      tr.appendChild(el('td', members.length ? '' : 'muted', members.join(', ') || 'None yet'));
      const act = el('td');
      if (can('signage.manage')) {
        act.appendChild(button('Delete', 'danger', async function () {
          if (!(await relayUI.confirm('Delete the group "' + g.name + '"? Its screens go back to their own playlists.'))) return;
          call(async function () { await send('DELETE', '/api/admin/signage/groups/' + g.id); await refresh(); });
        }));
      }
      tr.appendChild(act);
      body.appendChild(tr);
    }
  }
  $('sgGroupAdd').onclick = function () {
    call(async function () {
      await post('/api/admin/signage/groups', { name: $('sgGroupName').value });
      $('sgGroupName').value = '';
      await refresh();
    });
  };

  /* ---------------------------------------------------------------- playlists */
  function renderPlaylists() {
    const box = $('sgPlaylists');
    box.textContent = '';
    for (const p of data.playlists) {
      const b = button(p.name + ' (' + p.items.length + ')', '', async function () {
        if (dirty && current !== p.id && !(await relayUI.confirm('Leave without saving the changes to this playlist?'))) return;
        openPlaylist(p.id);
      });
      b.setAttribute('aria-current', String(p.id === current));
      box.appendChild(b);
    }
    if (current && !data.playlists.some(function (p) { return p.id === current; })) { current = null; draft = null; dirty = false; }
    if (!current && data.playlists.length) openPlaylist(data.playlists[0].id);
    else if (current && !dirty) openPlaylist(current);
    else renderEditor();
  }

  function openPlaylist(id) {
    const p = data.playlists.find(function (x) { return x.id === id; });
    current = id;
    draft = p.items.map(function (it) { return Object.assign({}, it); });
    dirty = false;
    if (document.activeElement !== $('sgRename')) $('sgRename').value = p.name;
    for (const b of $('sgPlaylists').children) b.setAttribute('aria-current', String(b.textContent.indexOf(p.name + ' (') === 0));
    renderEditor();
  }

  function markDirty() {
    dirty = true;
    $('sgItemsSave').disabled = false;
    $('sgItemsState').textContent = 'Not saved yet';
  }

  function renderEditor() {
    $('sgEditor').hidden = !current;
    if (!current) return;
    const p = data.playlists.find(function (x) { return x.id === current; });
    $('sgEditorTitle').textContent = p.name;
    $('sgItemsSave').disabled = !dirty;
    $('sgItemsState').textContent = dirty ? 'Not saved yet' : '';
    const box = $('sgItems');
    box.textContent = '';
    $('sgNoItems').hidden = draft.length > 0;
    const manage = can('signage.manage');
    draft.forEach(function (it, i) {
      const row = el('div', 'sg-item' + (it.missing ? ' off' : ''));
      row.appendChild(thumb(it.thumb, it.missing ? 'Off' : ''));
      const name = el('div', 'sg-iname');
      name.appendChild(el('div', '', it.name));
      const out = (it.end_date && it.end_date < data.today) ? 'Ended' : (it.start_date && it.start_date > data.today ? 'Not started' : '');
      name.appendChild(el('div', 'muted', (it.missing ? 'Not available: ' : '') + (it.source_label || '') + (out ? ' · ' + out : '')));
      row.appendChild(name);
      function field(label, input) {
        const f = el('div', 'field'); const l = el('label', '', label); f.appendChild(l); f.appendChild(input); return f;
      }
      const dur = el('input'); dur.type = 'number'; dur.min = '0'; dur.max = '86400'; dur.step = '1'; dur.value = it.duration_s;
      dur.oninput = function () { it.duration_s = Number(dur.value) || 0; markDirty(); };
      row.appendChild(field('Seconds', dur));
      const start = el('input'); start.type = 'date'; start.value = it.start_date || '';
      start.onchange = function () { it.start_date = start.value; markDirty(); };
      row.appendChild(field('From', start));
      const end = el('input'); end.type = 'date'; end.value = it.end_date || '';
      end.onchange = function () { it.end_date = end.value; markDirty(); };
      row.appendChild(field('Until', end));
      const tr = el('select'); option(tr, 'fade', 'Fade', it.transition === 'fade'); option(tr, 'cut', 'Cut', it.transition === 'cut');
      tr.onchange = function () { it.transition = tr.value; markDirty(); };
      row.appendChild(field('Transition', tr));
      const fit = el('select'); option(fit, 'fit', 'Fit (letterbox)', it.fit === 'fit'); option(fit, 'fill', 'Fill (crop)', it.fit === 'fill');
      fit.onchange = function () { it.fit = fit.value; markDirty(); };
      row.appendChild(field('Fit', fit));
      const ctl = el('div', 'ctl');
      ctl.appendChild(button('Up', '', function () { if (i > 0) { draft.splice(i - 1, 0, draft.splice(i, 1)[0]); markDirty(); renderEditor(); } }));
      ctl.appendChild(button('Down', '', function () { if (i < draft.length - 1) { draft.splice(i + 1, 0, draft.splice(i, 1)[0]); markDirty(); renderEditor(); } }));
      ctl.appendChild(button('Remove', 'danger', function () { draft.splice(i, 1); markDirty(); renderEditor(); }));
      row.appendChild(ctl);
      for (const x of row.querySelectorAll('input,select,button')) x.disabled = !manage;
      box.appendChild(row);
    });
    renderPicker();
  }

  function addItem(src, ref, name, thumbUrl, label, kind) {
    draft.push({ source: src, ref: ref, name: name, thumb: thumbUrl, source_label: label,
      duration_s: kind === 'video' ? 0 : 10, start_date: '', end_date: '', transition: 'fade', fit: 'fit' });
    markDirty();
    renderEditor();
  }

  async function renderPicker() {
    const sel = $('sgSource');
    const others = data.sources.filter(function (s) { return s.kind !== 'media'; });
    $('sgSourcePick').hidden = others.length === 0;
    if (sel.options.length !== data.sources.length) {
      sel.textContent = '';
      for (const s of data.sources) option(sel, s.kind, s.label, s.kind === source);
    }
    const grid = $('sgPicker');
    grid.textContent = '';
    if (source === 'media') {
      if (!data.media.length) { grid.appendChild(el('p', 'note', 'Upload images or videos on the Media tab first.')); return; }
      for (const m of data.media) {
        const c = el('div', 'sg-card pick');
        c.title = 'Add ' + m.name;
        c.appendChild(thumb(m.has_thumb ? '/api/admin/signage/media/' + m.id + '/thumb' : '', m.kind));
        c.appendChild(el('div', 'sg-name', m.name));
        c.appendChild(el('div', 'muted', m.kind === 'video' ? 'Video' : 'Image'));
        c.onclick = function () {
          addItem('media', m.id, m.name, m.has_thumb ? '/api/admin/signage/media/' + m.id + '/thumb' : '', 'Uploaded image or video', m.kind);
        };
        grid.appendChild(c);
      }
      return;
    }
    const src = data.sources.find(function (s) { return s.kind === source; });
    const res = await call(function () { return api('/api/admin/signage/sources/' + encodeURIComponent(source)); });
    if (!res) return;
    if (!res.choices.length) grid.appendChild(el('p', 'note', 'Nothing to add from ' + src.label + ' right now.'));
    for (const ch of res.choices) {
      const c = el('div', 'sg-card pick');
      c.appendChild(thumb(ch.thumb || '', src.label));
      c.appendChild(el('div', 'sg-name', ch.name || ch.ref));
      c.onclick = function () { addItem(source, ch.ref, ch.name || ch.ref, ch.thumb || '', src.label, ch.kind || ''); };
      grid.appendChild(c);
    }
  }
  $('sgSource').onchange = function () { source = $('sgSource').value; renderPicker(); };

  $('sgItemsSave').onclick = function () {
    const items = draft.map(function (it) {
      return { source: it.source, ref: it.ref, duration_s: it.duration_s, start_date: it.start_date || '',
        end_date: it.end_date || '', transition: it.transition, fit: it.fit };
    });
    call(async function () {
      await send('PUT', '/api/admin/signage/playlists/' + current + '/items', { items: items });
      dirty = false;
      await refresh();
      $('sgItemsState').textContent = 'Saved';
    });
  };
  $('sgPlaylistAdd').onclick = function () {
    call(async function () {
      const p = await post('/api/admin/signage/playlists', { name: $('sgPlaylistName').value });
      $('sgPlaylistName').value = '';
      current = p.id; dirty = false;
      await refresh();
    });
  };
  $('sgRenameSave').onclick = function () {
    call(async function () { await send('PATCH', '/api/admin/signage/playlists/' + current, { name: $('sgRename').value }); await refresh(); });
  };
  $('sgDuplicate').onclick = async function () {
    if (dirty && !(await relayUI.confirm('The copy is made from the saved playlist, without your unsaved changes. Go on?'))) return;
    call(async function () {
      const p = await post('/api/admin/signage/playlists/' + current + '/duplicate', {});
      current = p.id; dirty = false;
      await refresh();
    });
  };
  $('sgPlaylistDelete').onclick = async function () {
    const p = data.playlists.find(function (x) { return x.id === current; });
    if (!(await relayUI.confirm('Delete the playlist "' + p.name + '"? Screens and groups playing it will play nothing.'))) return;
    call(async function () { await send('DELETE', '/api/admin/signage/playlists/' + current); current = null; dirty = false; await refresh(); });
  };
  $('sgPreviewList').onclick = function () {
    if (dirty) { showError('Save the playlist to preview your changes.'); }
    const p = data.playlists.find(function (x) { return x.id === current; });
    openPreview({ playlist: current }, p.name);
  };

  /* ---------------------------------------------------------------- media */
  function renderMedia() {
    const st = data.storage;
    $('sgStorage').textContent = size(st.used) + ' used · ' + (st.free >= 0 ? size(st.free) + ' free on this disk' : 'free space unknown');
    $('sgLowDisk').hidden = !st.low;
    const grid = $('sgMedia');
    grid.textContent = '';
    $('sgNoMedia').hidden = data.media.length > 0;
    const manage = can('signage.manage');
    for (const m of data.media) {
      const c = el('div', 'sg-card');
      c.appendChild(thumb(m.has_thumb ? '/api/admin/signage/media/' + m.id + '/thumb' : '', m.kind));
      c.appendChild(el('div', 'sg-name', m.name));
      const bits = [m.kind === 'video' ? 'Video' : 'Image', size(m.size)];
      if (m.width && m.height) bits.push(m.width + '×' + m.height);
      if (m.kind === 'video' && m.duration_s) bits.push(Math.round(m.duration_s) + 's');
      c.appendChild(el('div', 'muted', bits.join(' · ')));
      c.appendChild(el('div', 'muted', m.uses ? 'In ' + m.uses + (m.uses === 1 ? ' playlist item' : ' playlist items') : 'Not in a playlist'));
      if (manage) {
        const ctl = el('div', 'ctl');
        ctl.appendChild(button('Rename', '', function () {
          const name = prompt('Name', m.name);
          if (name === null) return;
          call(async function () { await send('PATCH', '/api/admin/signage/media/' + m.id, { name: name }); await refresh(); });
        }));
        ctl.appendChild(button('Delete', 'danger', async function () {
          if (!(await relayUI.confirm('Delete "' + m.name + '" from the library?'))) return;
          call(async function () { await send('DELETE', '/api/admin/signage/media/' + m.id); await refresh(); });
        }));
        c.appendChild(ctl);
      }
      grid.appendChild(c);
    }
  }

  /* A 320×180 WebP of the image or a frame a second into the video, with its size and length. */
  function describeFile(file) {
    return new Promise(function (resolve) {
      const info = { width: 0, height: 0, duration: 0, thumb: null };
      const url = URL.createObjectURL(file);
      // Only ever a blob: address made here from the chosen file.
      if (!/^blob:/.test(url)) { resolve(info); return; }
      function draw(src, w, h) {
        try {
          const c = document.createElement('canvas'); c.width = 320; c.height = 180;
          const g = c.getContext('2d');
          g.fillStyle = '#000'; g.fillRect(0, 0, 320, 180);
          const s = Math.max(320 / w, 180 / h), dw = w * s, dh = h * s;
          g.drawImage(src, (320 - dw) / 2, (180 - dh) / 2, dw, dh);
          c.toBlob(function (b) { info.thumb = b; done(); }, 'image/webp', 0.8);
        } catch (e) { done(); }
      }
      let finished = false;
      function done() { if (finished) return; finished = true; URL.revokeObjectURL(url); resolve(info); }
      setTimeout(done, 15000);
      if (file.type.indexOf('video/') === 0) {
        const v = document.createElement('video');
        v.muted = true; v.preload = 'auto'; v.playsInline = true;
        v.onloadedmetadata = function () {
          info.width = v.videoWidth; info.height = v.videoHeight; info.duration = isFinite(v.duration) ? v.duration : 0;
          v.currentTime = Math.min(1, (v.duration || 0) / 2);
        };
        v.onseeked = function () { if (v.videoWidth) draw(v, v.videoWidth, v.videoHeight); else done(); };
        v.onerror = done;
        v.src = encodeURI(url);
      } else {
        const i = new Image();
        i.onload = function () { info.width = i.naturalWidth; info.height = i.naturalHeight; draw(i, i.naturalWidth, i.naturalHeight); };
        i.onerror = done;
        i.src = encodeURI(url);
      }
    });
  }

  function upload(file, line) {
    return new Promise(function (resolve, reject) {
      describeFile(file).then(function (info) {
        const q = new URLSearchParams({ name: file.name.replace(/\.[^.]+$/, ''), width: info.width, height: info.height,
          duration: info.duration || 0 });
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/admin/signage/media?' + q.toString());
        xhr.setRequestHeader('Content-Type', 'application/octet-stream');
        xhr.upload.onprogress = function (ev) {
          if (ev.lengthComputable) line.textContent = file.name + ': ' + Math.round(100 * ev.loaded / ev.total) + '%';
        };
        xhr.onload = async function () {
          let body = {};
          try { body = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
          if (xhr.status !== 200) { reject(new Error(body.error || body.detail || ('HTTP ' + xhr.status))); return; }
          if (info.thumb) {
            try { await fetch('/api/admin/signage/media/' + body.id + '/thumb', { method: 'PUT', body: info.thumb }); } catch (e) { /* no thumbnail */ }
          }
          resolve(body);
        };
        xhr.onerror = function () { reject(new Error('The upload stopped. Check the connection to Relay.')); };
        xhr.send(file);
      });
    });
  }

  async function uploadAll(files) {
    const list = $('sgUploads');
    for (const f of files) {
      const line = el('div', '', f.name + ': waiting');
      list.appendChild(line);
      const limit = f.type.indexOf('video/') === 0 ? data.limits.video : data.limits.image;
      if (f.size > limit) { line.textContent = f.name + ': too large (' + size(f.size) + ', the limit is ' + size(limit) + ')'; line.className = 'err'; continue; }
      try {
        await upload(f, line);
        line.textContent = f.name + ': uploaded';
        line.className = 'ok';
        setTimeout(function () { line.remove(); }, 4000);
      } catch (e) {
        line.textContent = f.name + ': ' + e.message;
        line.className = 'err';
      }
      await refresh();
    }
  }
  $('sgFile').onchange = function () { const fs = Array.from($('sgFile').files); $('sgFile').value = ''; uploadAll(fs); };
  const drop = $('sgDrop');
  drop.ondragover = function (ev) { ev.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = function () { drop.classList.remove('over'); };
  drop.ondrop = function (ev) { ev.preventDefault(); drop.classList.remove('over'); uploadAll(Array.from(ev.dataTransfer.files)); };

  /* ---------------------------------------------------------------- settings */
  $('sgPortSave').onclick = function () {
    call(async function () {
      await send('PUT', '/api/admin/signage/settings', { port: Number($('sgPort').value) });
      await refresh();
    });
  };


  /* ---------------------------------------------------------------- schedule */
  const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  let slotEditing = null;      // slot id being edited, 0 for a new one, null when the form is closed
  function zoneLabel() {
    const z = data.timezones.find(function (x) { return x.id === data.settings.timezone; });
    return z ? z.label : data.settings.timezone;
  }
  function targetOptions(sel, value) {
    sel.textContent = '';
    option(sel, 'all:0', 'All screens', value === 'all:0');
    for (const g of data.groups) option(sel, 'group:' + g.id, 'Group: ' + g.name, value === 'group:' + g.id);
    for (const s of data.screens) option(sel, 'screen:' + s.id, 'Screen: ' + s.name, value === 'screen:' + s.id);
  }
  function targetName(t, id) {
    if (t === 'all') return 'All screens';
    const list = t === 'group' ? data.groups : data.screens;
    const x = list.find(function (y) { return y.id === id; });
    return (t === 'group' ? 'Group: ' : 'Screen: ') + (x ? x.name : '?');
  }
  function planName(id) {
    const p = data.playlists.find(function (x) { return x.id === id; });
    return p ? p.name : '?';
  }
  function renderSchedule() {
    $('sgZoneName').textContent = zoneLabel();
    const body = $('sgSlots');
    body.textContent = '';
    $('sgNoSlots').hidden = data.slots.length > 0;
    for (const sl of data.slots) {
      const tr = el('tr');
      tr.appendChild(el('td', '', targetName(sl.target, sl.target_id)));
      tr.appendChild(el('td', '', sl.summary));
      tr.appendChild(el('td', '', planName(sl.playlist_id)));
      const act = el('td');
      if (can('signage.manage')) {
        act.appendChild(button('Edit', '', function () { openSlotForm(sl); }));
        act.appendChild(button('Delete', 'danger', async function () {
          if (!(await relayUI.confirm('Delete this time slot?'))) return;
          call(async function () { await send('DELETE', '/api/admin/signage/slots/' + sl.id); await refresh(); });
        }));
      }
      tr.appendChild(act);
      body.appendChild(tr);
    }
    if (document.activeElement !== $('sgOvTarget') && document.activeElement !== $('sgOvPlaylist')) {
      targetOptions($('sgOvTarget'), $('sgOvTarget').value);
      playlistSelect($('sgOvPlaylist'), Number($('sgOvPlaylist').value) || null, 'Choose a playlist');
    }
    const box = $('sgOverrides');
    box.textContent = '';
    for (const o of data.overrides) {
      const row = el('div', 'sg-row');
      row.appendChild(el('span', '', targetName(o.target, o.target_id) + ' plays ' + planName(o.playlist_id) +
        (o.expires_at ? ' until ' + new Date(o.expires_at).toLocaleString() : ' until cleared')));
      if (can('signage.manage')) {
        row.appendChild(button('Clear', '', function () {
          call(async function () {
            await send('DELETE', '/api/admin/signage/override?target=' + o.target + '&target_id=' + o.target_id);
            await refresh();
          });
        }));
      }
      box.appendChild(row);
    }
  }
  function syncSlotForm() {
    const range = $('sgSlotKind').value === 'range';
    $('sgSlotFromBox').hidden = !range; $('sgSlotToBox').hidden = !range;
    $('sgSlotTimes').hidden = $('sgSlotAllDay').checked;
  }
  function openSlotForm(sl) {
    slotEditing = sl ? sl.id : 0;
    $('sgSlotForm').hidden = false;
    targetOptions($('sgSlotTarget'), sl ? sl.target + ':' + sl.target_id : '');
    playlistSelect($('sgSlotPlaylist'), sl ? sl.playlist_id : null, 'Choose a playlist');
    $('sgSlotKind').value = sl && sl.start_date ? 'range' : 'weekly';
    $('sgSlotFrom').value = sl ? sl.start_date : '';
    $('sgSlotTo').value = sl ? sl.end_date : '';
    const days = $('sgSlotDays');
    for (const c of days.querySelectorAll('label')) c.remove();
    const on = sl ? sl.days.split(',').filter(Boolean).map(Number) : [];
    DAYS.forEach(function (name, i) {
      const l = el('label', 'check');
      const c = el('input'); c.type = 'checkbox'; c.value = String(i); c.checked = on.indexOf(i) >= 0;
      l.appendChild(c); l.appendChild(document.createTextNode(' ' + name));
      l.style.marginRight = '12px';
      days.appendChild(l);
    });
    $('sgSlotAllDay').checked = !(sl && sl.start_time);
    $('sgSlotStart').value = sl ? sl.start_time : '09:00';
    $('sgSlotEnd').value = sl ? sl.end_time : '11:30';
    syncSlotForm();
  }
  $('sgSlotKind').onchange = syncSlotForm;
  $('sgSlotAllDay').onchange = syncSlotForm;
  $('sgSlotAdd').onclick = function () { openSlotForm(null); };
  $('sgSlotCancel').onclick = function () { slotEditing = null; $('sgSlotForm').hidden = true; };
  $('sgSlotForm').onsubmit = function (ev) {
    ev.preventDefault();
    const parts = $('sgSlotTarget').value.split(':');
    const range = $('sgSlotKind').value === 'range';
    const allDay = $('sgSlotAllDay').checked;
    const payload = {
      target: parts[0], target_id: Number(parts[1]), playlist_id: Number($('sgSlotPlaylist').value) || 0,
      days: Array.from($('sgSlotDays').querySelectorAll('input:checked')).map(function (c) { return Number(c.value); }),
      start_date: range ? $('sgSlotFrom').value : '', end_date: range ? $('sgSlotTo').value : '',
      start_time: allDay ? '' : $('sgSlotStart').value, end_time: allDay ? '' : $('sgSlotEnd').value
    };
    call(async function () {
      if (slotEditing) await send('PUT', '/api/admin/signage/slots/' + slotEditing, payload);
      else await post('/api/admin/signage/slots', payload);
      slotEditing = null; $('sgSlotForm').hidden = true;
      await refresh();
    });
  };
  $('sgOvSet').onclick = function () {
    const parts = $('sgOvTarget').value.split(':');
    call(async function () {
      await send('PUT', '/api/admin/signage/override', { target: parts[0], target_id: Number(parts[1]), playlist_id: Number($('sgOvPlaylist').value) || 0 });
      await refresh();
    });
  };
  /* ---------------------------------------------------------------- takeovers (#207) */
  function renderTakeover() {
    const box = $('sgTaken');
    box.textContent = '';
    const taken = data.screens.filter(function (s) { return s.taken_over; });
    $('sgNoTaken').hidden = taken.length > 0;
    $('sgReturnAll').disabled = data.takeovers.length === 0;
    for (const t of data.takeovers) {
      const row = el('div', 'sg-row');
      const n = data.screens.filter(function (s) { return s.taken_over && s.taken_over.from === t.target; }).length;
      row.appendChild(el('span', '', targetName(t.target, t.target_id) + ': ' + t.label +
        (t.kind === 'push' ? (t.mode === 'once' ? ' (once)' : t.expires_at ? ' until ' + new Date(t.expires_at).toLocaleTimeString() : ' until cleared') : ' until cleared') +
        (n ? '' : ' (no screen shows it now)')));
      if (can('signage.manage')) {
        row.appendChild(button('Return to playlist', '', function () {
          call(async function () {
            await send('DELETE', '/api/admin/signage/takeover?target=' + t.target + '&target_id=' + t.target_id);
            await refresh();
          });
        }));
      }
      box.appendChild(row);
    }
    if (document.activeElement !== $('sgPushTarget') && document.activeElement !== $('sgPushMedia') &&
        document.activeElement !== $('sgLiveTarget') && document.activeElement !== $('sgLiveView')) {
      targetOptions($('sgPushTarget'), $('sgPushTarget').value);
      targetOptions($('sgLiveTarget'), $('sgLiveTarget').value);
      const media = $('sgPushMedia'), keep = media.value;
      media.textContent = '';
      option(media, '', 'Choose a file', !keep);
      for (const m of data.media) option(media, m.id, m.name, m.id === keep);
      const view = $('sgLiveView'), vkeep = view.value;
      view.textContent = '';
      for (const v of data.views) option(view, v.id, v.label, v.id === vkeep);
    }
    $('sgPushSecsBox').hidden = $('sgPushMode').value !== 'seconds';
    const flashing = data.screens.filter(function (s) { return s.banner; }).length;
    $('sgAlertNow').textContent = flashing ? 'An alert is showing on ' + flashing + (flashing === 1 ? ' screen.' : ' screens.')
      : 'No alert is showing on a screen.';
    const mode = $('sgAlertMode');
    if (document.activeElement !== mode) mode.value = data.settings.alert_screens;
    const gbox = $('sgAlertGroups');
    gbox.hidden = mode.value !== 'groups';
    if (!gbox.contains(document.activeElement)) {
      for (const n of Array.from(gbox.querySelectorAll('label'))) n.remove();
      for (const g of data.groups) {
        const l = el('label', 'check'); const c = el('input'); c.type = 'checkbox'; c.value = g.id;
        c.checked = data.settings.alert_groups.indexOf(g.id) >= 0;
        l.appendChild(c); l.appendChild(document.createTextNode(' ' + g.name)); gbox.appendChild(l);
      }
    }
  }
  $('sgPushMode').onchange = function () { $('sgPushSecsBox').hidden = $('sgPushMode').value !== 'seconds'; };
  $('sgAlertMode').onchange = function () { $('sgAlertGroups').hidden = $('sgAlertMode').value !== 'groups'; };
  $('sgPushGo').onclick = function () {
    const parts = $('sgPushTarget').value.split(':');
    call(async function () {
      await send('PUT', '/api/admin/signage/takeover', { kind: 'push', target: parts[0], target_id: Number(parts[1]),
        media_id: $('sgPushMedia').value, mode: $('sgPushMode').value, seconds: Number($('sgPushSecs').value) || 0 });
      await refresh();
    });
  };
  $('sgLiveGo').onclick = function () {
    const parts = $('sgLiveTarget').value.split(':');
    call(async function () {
      await send('PUT', '/api/admin/signage/takeover', { kind: 'live', target: parts[0], target_id: Number(parts[1]),
        view: $('sgLiveView').value });
      await refresh();
    });
  };
  $('sgReturnAll').onclick = async function () {
    if (!(await relayUI.confirm('Return every screen to its playlist?'))) return;
    call(async function () { await send('DELETE', '/api/admin/signage/takeovers'); await refresh(); });
  };
  $('sgAlertSave').onclick = function () {
    const ids = Array.from($('sgAlertGroups').querySelectorAll('input:checked')).map(function (c) { return Number(c.value); });
    call(async function () {
      await send('PUT', '/api/admin/signage/settings', { alert_screens: $('sgAlertMode').value, alert_groups: ids });
      await refresh();
    });
  };
  $('sgSchedSave').onclick = function () {
    call(async function () {
      await send('PUT', '/api/admin/signage/settings', { timezone: $('sgZone').value, override_until: $('sgOvUntil').value });
      await refresh();
    });
  };

  /* ---------------------------------------------------------------- preview */
  async function openPreview(q, title) {
    if (typeof window.SignagePlayer !== 'function') return;
    const state = await call(function () { return api('/api/admin/signage/preview?' + new URLSearchParams(q).toString()); });
    if (!state) return;
    closePreview();
    $('sgPreview').hidden = false;
    $('sgPreviewTitle').textContent = title + (state.items.length ? '' : ': nothing to play today');
    player = new window.SignagePlayer($('sgStage'), { preview: true });
    player.load(state);
  }
  function closePreview() {
    if (player) { player.stop(); player = null; }
    $('sgPreview').hidden = true;
  }
  $('sgPreviewClose').onclick = closePreview;
  $('sgPreview').onclick = function (ev) { if (ev.target === $('sgPreview')) closePreview(); };
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && player) closePreview(); });

  /* ---------------------------------------------------------------- refresh */
  async function refresh() {
    try { data = await api('/api/admin/signage'); render(); showError(''); }
    catch (e) { showError(e.message); }
  }
  function visible() { return !page.closest('.page').hidden && !page.hidden; }
  function tick() {
    // Keep "now playing" fresh on the Screens tab, never redrawing under someone filling in the screen form or
    // editing a playlist.
    if (visible() && editing === null && slotEditing === null && (tab === 'screens' || tab === 'schedule' || tab === 'takeover')) refresh();
    timer = setTimeout(tick, 10000);
  }
  document.addEventListener('relay:config', function () { refresh(); if (!timer) timer = setTimeout(tick, 10000); });
  if (R.cfg()) { refresh(); timer = setTimeout(tick, 10000); }
})();
