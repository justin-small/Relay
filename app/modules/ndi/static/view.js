/* The NDI single-source viewer page (#296): the MJPEG picture, an audio meter and the tally border. */
(function () {
  const d = JSON.parse(document.getElementById('ndvData').textContent);
  const root = document.getElementById('ndv'), img = document.getElementById('ndvImg');
  const info = document.getElementById('ndvInfo'), msg = document.getElementById('ndvMsg');
  const meter = document.getElementById('ndvMeter'), bar = meter.firstElementChild;
  const enc = encodeURIComponent(d.source);
  let retry = 1000;
  function play() {
    img.src = '/ndi/stream/' + enc + '?t=' + Date.now();
  }
  img.onload = function () { msg.textContent = ''; retry = 1000; };
  img.onerror = function () {
    msg.textContent = 'Waiting for the source...';
    setTimeout(play, retry);
    retry = Math.min(10000, retry * 2);
  };
  play();
  async function level() {
    try {
      const res = await fetch('/ndi/level/' + enc, { cache: 'no-store', credentials: 'same-origin' });
      if (res.ok) {
        const m = await res.json();
        info.textContent = m.width ? m.width + 'x' + m.height + (m.fps ? ' ' + m.fps + ' fps' : '') : '';
        meter.hidden = typeof m.db !== 'number';
        if (typeof m.db === 'number') bar.style.width = Math.max(0, Math.min(100, (m.db + 60) / 60 * 100)) + '%';
      }
    } catch (e) { /* the next tick tries again */ }
    setTimeout(level, 400);
  }
  level();
  if (d.camera) {            // tally from the shared state the switcher modules fill (app/tally.py)
    let es = null;
    const open = function () {
      es = new EventSource('/stream?stream=tally');
      es.onmessage = function (ev) {
        let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
        const c = (m.cameras || []).filter(function (x) { return x.n === d.camera; })[0];
        root.dataset.tally = c ? (c.program ? 'program' : (c.preview ? 'preview' : '')) : '';
      };
      es.onerror = function () { es.close(); root.dataset.tally = ''; setTimeout(open, 3000); };
    };
    open();
  }
  img.addEventListener('dblclick', function () {
    if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen && root.requestFullscreen();
  });
})();
