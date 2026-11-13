/* The Remote access admin page (#50): enroll with a code, show the internet link with a QR code and whether the
   tunnel is up, release the address. Loaded by admin.html only while the module is on. */
(function () {
  const R = window.RelayAdmin;
  if (!R || !document.getElementById('page-remoteaccess')) return;
  const $ = R.$, api = R.api, post = R.post;
  function can(perm) { return typeof window.relayCan !== 'function' || !!window.relayCan(perm); }

  let last = null;
  let timer = null;

  function showError(text) {
    $('raErr').textContent = text || '';
    $('raErr').hidden = !text;
  }

  function paintQr(href) {
    const box = $('raQr');
    if (box.dataset.href === href) return;
    box.dataset.href = href;
    box.innerHTML = '';
    if (typeof qrcode !== 'function' || !href) return;
    try {
      const qr = qrcode(0, 'M');
      qr.addData(href);
      qr.make();
      box.innerHTML = qr.createSvgTag({ cellSize: 3, margin: 1, scalable: true });
    } catch (e) { /* the link text is the fallback */ }
  }

  function render(v) {
    last = v;
    const manage = can('remote_access.manage');
    $('raLinkBox').hidden = !v.enrolled;
    $('raEnrollBox').hidden = v.enrolled || !manage;
    $('raRelease').hidden = !manage;
    $('raAccessNote').hidden = manage || v.enrolled;
    $('raAccessNote').textContent = 'An admin enrolls this install to give it a remote link.';
    $('raSum').textContent = v.enrolled ? (v.state === 'connected' ? 'live' : 'not connected') : '';
    if (v.enrolled) {
      $('raUrl').textContent = v.url;
      $('raUrl').href = v.url;
      paintQr(v.url);
    }
    const text = v.cloud_error || v.detail;
    $('raState').textContent = text;
    $('raState').className = 'note ' + (v.state === 'connected' && !v.cloud_error ? 'ok'
      : (v.state === 'missing' || v.state === 'retrying' || v.cloud_error ? 'err' : ''));
  }

  async function refresh() {
    try { render(await api('/api/admin/remote-access')); } catch (e) { /* the admin page reports lost sessions */ }
  }

  function visible() { return !document.getElementById('page-remoteaccess').closest('.page').hidden; }

  function tick() {
    if (visible()) refresh();
    // Faster while the tunnel is coming up, so "Connected" shows promptly after enrolling.
    timer = setTimeout(tick, last && last.enrolled && last.state !== 'connected' ? 2000 : 10000);
  }

  $('raEnroll').addEventListener('click', async function () {
    const code = $('raCode').value.trim();
    if (!code) { showError('Enter the enrollment code you were given.'); return; }
    showError('');
    $('raEnroll').disabled = true;
    $('raEnroll').textContent = 'Enrolling…';
    try {
      render(await post('/api/admin/remote-access/enroll', { code: code }));
      $('raCode').value = '';
    } catch (e) {
      showError(e.message);
    } finally {
      $('raEnroll').disabled = false;
      $('raEnroll').textContent = 'Enroll';
    }
  });

  $('raRelease').addEventListener('click', async function () {
    if (!confirm('Release ' + (last && last.url) + '?\n\nThe link stops working for good, and enrolling again ' +
                 'needs a new code and gives a different link.')) return;
    showError('');
    try { render(await post('/api/admin/remote-access/release', {})); } catch (e) { showError(e.message); }
  });

  $('raCopy').addEventListener('click', function () {
    if (!last || !last.url) return;
    if (navigator.clipboard) navigator.clipboard.writeText(last.url).then(function () {
      $('raCopy').textContent = 'Copied';
      setTimeout(function () { $('raCopy').textContent = 'Copy link'; }, 1500);
    }, function () { /* the link stays selectable */ });
  });

  document.addEventListener('relay:config', function () { refresh(); if (!timer) tick(); });
})();
