/* Mockup only: switches between the three screens of one direction. The screen ids come from the buttons on the
   page, never from the URL. */
(function () {
  'use strict';
  const buttons = Array.prototype.slice.call(document.querySelectorAll('[data-s]'));
  const known = buttons.map(function (b) { return b.getAttribute('data-s'); });
  function show(id) {
    if (known.indexOf(id) < 0) return;
    buttons.forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-s') === id); });
    document.querySelectorAll('.scr').forEach(function (s) { s.classList.toggle('on', s.id === id); });
  }
  buttons.forEach(function (b) { b.addEventListener('click', function () { show(b.getAttribute('data-s')); }); });
  document.querySelectorAll('[data-go]').forEach(function (a) {
    a.addEventListener('click', function (e) { e.preventDefault(); show(a.getAttribute('data-go')); });
  });
})();
