/* /screen?lighting=<page> (#379): tap a button, the cue fires once. The screen is told only a short code. Drawn by
   core's shared layout renderer (#421), the same drawing as the editor on the Lighting page. */
(function () {
  const data = JSON.parse(document.getElementById('ltData').textContent);
  const page = data.page;
  const q = '?key=' + encodeURIComponent(data.key);
  const toast = document.getElementById('ltToast');
  const SAYS = { unreachable: 'The lighting program did not answer.', refused: 'That button does not work.',
                 slow: 'Too many taps. Wait a moment.' };
  let timer = 0;
  function say(code) {
    toast.textContent = SAYS[code] || 'That did not work.';
    toast.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(function () { toast.hidden = true; }, 4000);
  }
  const items = page.cues.map(function (c) {
    return { id: c.id, kind: 'button', title: c.name, x: c.x, y: c.y, w: c.w, h: c.h, style: c.style };
  });
  const buttons = window.RelayLayout.draw(document.getElementById('ltScreen'), { cols: page.cols, rows: page.rows, items: items },
    async function (it) {
      try {
        const res = await fetch('/lighting/_/press/' + encodeURIComponent(page.id) + q, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cue: it.id }) });
        if (!res.ok) { const out = await res.json().catch(function () { return {}; }); say(out.error); }
      } catch (e) { say('unreachable'); }
    });
  async function poll() {
    try {
      const res = await fetch('/lighting/_/state/' + encodeURIComponent(page.id) + q, { cache: 'no-store' });
      if (res.ok) {
        const st = (await res.json()).state || {};
        Object.keys(buttons).forEach(function (id) { window.RelayLayout.update(buttons[id], { lit: !!st[id] }); });
      }
    } catch (e) { /* the next tick tries again */ }
  }
  poll();
  setInterval(poll, 3000);
})();
