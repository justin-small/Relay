/* /board/<slug> (#156): the board on a TV or tablet, drawn by the shared renderer, nothing but the board. Which page
   it shows comes from the server (a schedule, ProPresenter, or a page chosen on the Boards page); a rotation turns
   from the clock so every screen turns together. The screen reports itself with its own id, so the Boards page can
   name it, and loads the board again when it is saved or when "Reload all screens" is pressed. Laying a board out
   happens on the Boards page. Every value from the server is set as text. */
(function () {
  'use strict';
  const R = window.RelayLayout;
  const data = JSON.parse(document.getElementById('boardData').textContent);
  const board = data.board;
  const host = document.getElementById('board');
  const toast = document.getElementById('toast');
  const base = '/board/' + encodeURIComponent(board.id);
  const press = {};
  (data.press || []).forEach(function (t) { press[t] = true; });
  const live = {};          // item id -> the last data for it, re-applied whenever a page is drawn again
  let nodes = {};
  let shown = null;         // the page drawn now
  let state = data.state || {};
  let offset = 0;           // the server's clock less this one's, in seconds
  let reloadSeen = null;
  let toastTimer = 0;

  // This screen's own id, kept by the browser so the name an operator gives it holds. A browser that keeps nothing
  // gets a new one each time it loads.
  function screenId() {
    const key = 'relay.board.screen';
    let id = '';
    // A link from a screen's own address (Screens page, a signage player handing over) names the screen: "screen-<id>".
    const linked = new URLSearchParams(location.search).get('screen') || '';
    if (/^screen-[0-9]{1,9}$/.test(linked)) return linked;
    try { id = localStorage.getItem(key) || ''; } catch (e) { id = ''; }
    if (!/^[A-Za-z0-9_-]{8,40}$/.test(id)) {
      const a = new Uint8Array(12); crypto.getRandomValues(a);
      id = Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
      try { localStorage.setItem(key, id); } catch (e) { /* this load keeps it */ }
    }
    return id;
  }

  function say(text) {
    toast.textContent = text; toast.hidden = !text;
    clearTimeout(toastTimer);
    if (text) toastTimer = setTimeout(function () { toast.hidden = true; }, 4000);
  }
  function apply(id, d) {
    live[id] = Object.assign(live[id] || {}, d);
    if (nodes[id]) R.update(nodes[id], live[id]);
  }

  async function onPress(it, w, body) {
    if (it.type === 'link') {
      const to = (it.props || {}).board;
      if (to) location.href = '/board/' + encodeURIComponent(to);
      return;
    }
    if (!press[it.type]) { say('Sign in with an account that may press this.'); return; }
    try {
      const init = { method: 'POST', credentials: 'same-origin' };
      if (body) { init.headers = { 'Content-Type': 'application/json' }; init.body = JSON.stringify(body); }
      const res = await fetch(base + '/_/press/' + encodeURIComponent(it.id), init);
      if (!res.ok) { const out = await res.json().catch(function () { return {}; }); say(out.error || 'That did not work.'); }
    } catch (e) { say('Relay did not answer.'); }
  }

  function page(id) { return board.pages.filter(function (p) { return p.id === id; })[0] || board.pages[0]; }
  function want() { return (state.rotate && RelayBoardLive.rotation(state.rotate, offset)) || state.page; }

  // The board keeps its shape (16:9, 4:3, 9:16) whatever the window: the largest box of that shape that fits.
  function fit() {
    const g = host.querySelector('#boardView > .rl-grid');
    if (!g || !g.parentElement) return;
    const r = board.ratio.split(':').map(Number), box = g.parentElement, cs = getComputedStyle(box);
    const w = box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const h = box.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const s = Math.max(0, Math.min(w / r[0], h / r[1]));
    g.style.width = Math.floor(s * r[0]) + 'px'; g.style.height = Math.floor(s * r[1]) + 'px';
  }
  window.addEventListener('resize', fit);

  function draw() {
    const p = page(want());
    if (shown === p.id) return;
    shown = p.id;
    host.textContent = '';
    const v = document.createElement('div'); v.id = 'boardView'; host.appendChild(v);
    if (!p.items.length) {
      const e = document.createElement('div'); e.className = 'empty'; e.textContent = 'This page has nothing on it yet.';
      v.appendChild(e); nodes = {}; feeds.refresh(); return;
    }
    const g = document.createElement('div'); g.style.aspectRatio = board.ratio.replace(':', '/'); v.appendChild(g);
    nodes = R.draw(g, { cols: board.cols, rows: board.rows, items: p.items }, onPress);
    Object.keys(nodes).forEach(function (id) { if (live[id]) R.update(nodes[id], live[id]); });
    fit();
    feeds.refresh();
  }

  const feeds = RelayBoardLive.start({
    base: base, screen: screenId(), apply: apply,
    items: function () { return shown ? page(shown).items : []; },
    onPoll: function (got) {
      if (got.goto) { location.replace(got.goto); return; }   // this screen is set to show something else now (#527)
      if (got.rev && got.rev !== board.rev) { location.reload(); return; }
      if (reloadSeen === null) reloadSeen = got.reload;
      else if (got.reload !== reloadSeen) { location.reload(); return; }
      if (typeof got.now === 'number') offset = got.now - Date.now() / 1000;
      state = { page: got.page, rotate: got.rotate };
      draw();
    }
  });
  setInterval(function () { if (state.rotate) draw(); }, 500);
  draw();
})();
