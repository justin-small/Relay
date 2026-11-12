/* A /screen or /overlay opened with ?lyrics=1: ProPresenter's current slide as lyrics, in place of the captions
   while a song is up (propresenter.py's LyricsFeed). Nothing here when the operator has the feed switched off for
   this page: the stream refuses (403) and this script stops asking. */
(function () {
  'use strict';

  const audience = document.currentScript.dataset.for || 'screen';
  const box = document.getElementById('lyrics');
  const text = document.getElementById('lyricsText');
  if (!box || !text) return;

  function show(msg) {
    const on = !!(msg && msg.enabled && msg.live && msg.text);
    text.textContent = on ? msg.text : '';
    box.hidden = !on;
    document.body.classList.toggle('lyrics-on', on);
  }

  let es = null;
  function connect() {
    if (es) es.close();
    es = new EventSource('/stream?stream=lyrics&for=' + audience);
    es.onmessage = function (ev) {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === 'lyrics') show(msg);
    };
    es.onerror = function () {
      show(null);
      // A refusal ends the stream for good; a dropped connection is retried by the browser.
      if (es.readyState === EventSource.CLOSED) setTimeout(connect, 15000);
    };
  }
  connect();
})();
