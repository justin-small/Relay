/* The Chat tab of the crew page (#230). The newest messages are loaded when the tab opens, then a one-way event
   stream brings new ones; the stream is closed whenever the tab or the page is hidden, which is also what lets Relay
   send a quiet notification instead. Everything from the server is put on the page as text. */
(function () {
  'use strict';
  const root = document.getElementById('chat');
  if (!root) return;
  const $ = function (id) { return document.getElementById(id); };
  const sel = $('chatChannel'), list = $('chatList'), note = $('chatNote'), box = $('chatText'), push = $('chatPush');
  let me = 0, moderator = false, channels = [], current = 'team', source = null, retry = 0, timer = null;

  async function api(method, path, body) {
    const res = await fetch(path, { method: method, credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    if (res.status === 401 || res.status === 403 && method === 'GET') { location.reload(); return null; }
    const data = await res.json().catch(function () { return {}; });
    if (!res.ok) { note.textContent = data.detail || 'That did not work.'; return null; }
    note.textContent = '';
    return data;
  }
  function clock(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  function atBottom() { return list.scrollHeight - list.scrollTop - list.clientHeight < 60; }

  function draw(m) {
    const li = document.createElement('li');
    li.dataset.id = m.id; li.className = 'chat-msg' + (m.user_id === me ? ' mine' : '');
    const who = document.createElement('div'); who.className = 'chat-who';
    who.textContent = m.author + (m.role ? ' · ' + m.role : '') + ' · ' + clock(m.ts);
    const text = document.createElement('div'); text.className = 'chat-body'; text.textContent = m.body;
    li.appendChild(who); li.appendChild(text);
    if (m.user_id === me || moderator) {
      const del = document.createElement('button'); del.type = 'button'; del.className = 'chat-del'; del.textContent = 'Delete';
      del.setAttribute('aria-label', 'Delete message from ' + m.author);
      del.addEventListener('click', function () { api('DELETE', '/crew/api/chat/message/' + m.id); });
      li.appendChild(del);
    }
    return li;
  }
  function add(m) {
    if (m.channel !== current || list.querySelector('[data-id="' + m.id + '"]')) return;
    const stick = atBottom();
    list.appendChild(draw(m));
    if (stick) list.scrollTop = list.scrollHeight;
  }
  async function load() {
    const d = await api('GET', '/crew/api/chat/history?channel=' + encodeURIComponent(current));
    if (!d || d.channel !== current) return;
    list.textContent = '';
    d.messages.forEach(function (m) { list.appendChild(draw(m)); });
    list.scrollTop = list.scrollHeight;
    if (!d.messages.length) note.textContent = 'No messages yet.';
  }
  function showChannels() {
    sel.textContent = '';
    channels.forEach(function (c) { const o = document.createElement('option'); o.value = c.id; o.textContent = c.title; sel.appendChild(o); });
    if (!channels.some(function (c) { return c.id === current; })) current = 'team';
    sel.value = current;
    const c = channels.find(function (x) { return x.id === current; });
    push.checked = !!(c && c.push);
  }
  async function loadChannels() {
    const d = await api('GET', '/crew/api/chat/channels');
    if (!d) return false;
    me = d.me; moderator = d.moderator; channels = d.channels; showChannels(); return true;
  }

  function open() {
    close();
    if (!window.EventSource) { note.textContent = 'This browser cannot show new messages as they arrive.'; return; }
    source = new EventSource('/crew/api/chat/stream');
    source.onmessage = function (e) {
      let ev; try { ev = JSON.parse(e.data); } catch (x) { return; }
      if (ev.type === 'ready') { retry = 0; load(); }
      else if (ev.type === 'message') add(ev.message);
      else if (ev.type === 'delete') { const li = list.querySelector('[data-id="' + ev.id + '"]'); if (li && ev.channel === current) li.remove(); }
    };
    source.onerror = function () {      // the stream ended: reconnect with a growing pause, and reload on 'ready'
      close(); retry = Math.min(retry + 1, 6);
      timer = setTimeout(function () { if (active()) { loadChannels().then(open); } }, 1000 * retry);
    };
  }
  function close() { clearTimeout(timer); if (source) { source.close(); source = null; } }
  function active() { return !root.closest('[role=tabpanel]').hidden && document.visibilityState === 'visible'; }
  async function wake() {
    if (!active()) { close(); return; }
    if (await loadChannels()) open();
  }

  sel.addEventListener('change', function () { current = sel.value; showChannels(); note.textContent = ''; load(); });
  push.addEventListener('change', async function () {
    const d = await api('PUT', '/crew/api/chat/push', { channel: current, on: push.checked });
    if (d) { channels = d.channels; showChannels(); } else { push.checked = !push.checked; }
  });
  $('chatForm').addEventListener('submit', async function (e) {
    e.preventDefault();
    const body = box.value.trim();
    if (!body) return;
    $('chatSend').disabled = true;
    const d = await api('POST', '/crew/api/chat/send', { channel: current, body: body });
    $('chatSend').disabled = false;
    if (d) { box.value = ''; add(d.message); }
  });
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('chatForm').requestSubmit(); }
  });
  document.addEventListener('crew:tab', wake);
  document.addEventListener('visibilitychange', wake);
  wake();                                // the tab may already be showing: its event came before this script
})();
