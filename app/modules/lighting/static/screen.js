/* /screen?lighting=<page> (#379): tap a button, the cue fires once. The screen is told only a short code. */
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
  const drawn = window.LightingButtons.draw(document.getElementById('ltScreen'), page, async function (cue, b) {
    b.classList.add('sent');
    setTimeout(function () { b.classList.remove('sent'); }, 250);
    try {
      const res = await fetch('/lighting/_/press/' + encodeURIComponent(page.id) + q, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cue: cue.id }) });
      if (!res.ok) { const out = await res.json().catch(function () { return {}; }); say(out.error); }
    } catch (e) { say('unreachable'); }
  });
  async function poll() {
    try {
      const res = await fetch('/lighting/_/state/' + encodeURIComponent(page.id) + q, { cache: 'no-store' });
      if (res.ok) {
        const st = (await res.json()).state || {};
        Object.keys(drawn.buttons).forEach(function (id) { drawn.buttons[id].classList.toggle('lit', !!st[id]); });
      }
    } catch (e) { /* the next tick tries again */ }
  }
  poll();
  setInterval(poll, 3000);
})();
