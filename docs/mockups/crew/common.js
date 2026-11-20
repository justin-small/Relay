/* Crew mockups: every tab bar switches the panes in its own scope. A bar is [data-tabs]; its buttons carry data-p;
   the panes are .pane[data-pane] inside the nearest [data-scope]. Nothing is read from the URL. */
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-tabs] [data-p]');
    if (b) {
      var bar = b.closest('[data-tabs]'), scope = bar.closest('[data-scope]') || document;
      bar.querySelectorAll('[data-p]').forEach(function (x) {
        x.classList.toggle('on', x === b);
        x.setAttribute('aria-selected', x === b ? 'true' : 'false');
      });
      scope.querySelectorAll('.pane[data-pane]').forEach(function (p) {
        if (p.parentElement.closest('[data-scope]') === scope) p.classList.toggle('on', p.dataset.pane === b.dataset.p);
      });
      return;
    }
    var o = e.target.closest('.opts .opt, .seg button, .chans button');
    if (o) o.parentNode.querySelectorAll(o.tagName).forEach(function (x) { x.classList.toggle('on', x === o); });
  });
})();
