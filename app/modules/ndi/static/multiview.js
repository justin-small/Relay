/* The NDI multiview page (#297): one composed MJPEG picture. Double-click for full screen. */
(function () {
  const d = JSON.parse(document.getElementById('mvData').textContent);
  const root = document.getElementById('mv'), img = document.getElementById('mvImg'), msg = document.getElementById('mvMsg');
  let retry = 1000;
  function play() { img.src = '/ndi/multiview/' + encodeURIComponent(d.id) + '/stream?t=' + Date.now(); }
  img.onload = function () { msg.textContent = ''; retry = 1000; };
  img.onerror = function () { msg.textContent = 'Waiting for the multiview...'; setTimeout(play, retry); retry = Math.min(10000, retry * 2); };
  img.addEventListener('dblclick', function () {
    if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen && root.requestFullscreen();
  });
  play();
})();
