/* /screen?obs=<computer> (#250): tap a scene, OBS switches to it. The screen is told only a short code. */
(function () {
  const data = JSON.parse(document.getElementById('obsData').textContent);
  const mid = encodeURIComponent(data.machine.id);
  const q = '?key=' + encodeURIComponent(data.key);
  const box = document.getElementById('obsScenes');
  const toast = document.getElementById('obsToast');
  document.getElementById('obsName').textContent = data.machine.name;
  const SAYS = { unreachable: 'OBS did not answer.', refused: 'That scene is not in OBS any more.',
                 slow: 'Too many taps. Wait a moment.' };
  let timer = 0;
  let shown = '';
  function say(code) {
    toast.textContent = SAYS[code] || 'That did not work.';
    toast.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(function () { toast.hidden = true; }, 4000);
  }
  async function pick(scene, b) {
    b.classList.add('sent');
    setTimeout(function () { b.classList.remove('sent'); }, 250);
    try {
      const res = await fetch('/obs/_/scene/' + mid + q, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scene: scene }) });
      if (!res.ok) { const out = await res.json().catch(function () { return {}; }); say(out.error); }
      poll();
    } catch (e) { say('unreachable'); }
  }
  function draw(st) {
    const sig = JSON.stringify(st.scenes);
    if (sig !== shown) {
      shown = sig;
      box.textContent = '';
      st.scenes.forEach(function (name) {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'obsscene'; b.textContent = name; b.dataset.scene = name;
        b.onclick = function () { pick(name, b); };
        box.appendChild(b);
      });
    }
    Array.prototype.forEach.call(box.children, function (b) { b.classList.toggle('program', b.dataset.scene === st.program); });
    document.getElementById('obsLive').hidden = !st.streaming;
    document.getElementById('obsRec').hidden = !st.recording;
    document.getElementById('obsOff').hidden = st.connected;
  }
  async function poll() {
    try {
      const res = await fetch('/obs/_/state/' + mid + q, { cache: 'no-store' });
      if (res.ok) draw(await res.json());
    } catch (e) { /* the next tick tries again */ }
  }
  poll();
  setInterval(poll, 1500);
})();
