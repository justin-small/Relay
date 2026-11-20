/* The panel's look on a page that is not the operator panel (#434): sign-in, home, the alert sender and the other
   operator tools. The shell keeps a copy of the person's choices in this browser (localStorage "relayLook");
   this reads it back through the same allow-list as shell.js and the server, and sets the <html> attributes
   before first paint. Nothing here is markup. With no copy (a first visit) the Relay look, dark. */
(function (root) {
  'use strict';
  var CHOICES = {
    ui_preset: ['relay', 'softpop', 'tangerine', 'brutalist'],
    ui_mode: ['light', 'dark', 'system'],
    ui_font: ['system', 'rounded', 'serif', 'mono']
  };
  var DEFAULTS = { ui_preset: 'relay', ui_mode: 'dark', ui_font: 'system' };
  var FONTS = {
    rounded: 'ui-rounded,"SF Pro Rounded","Hiragino Maru Gothic ProN",system-ui,sans-serif',
    serif: 'ui-serif,Georgia,"Times New Roman",serif',
    mono: 'ui-monospace,SFMono-Regular,Menlo,monospace'
  };

  function pick(raw) {
    var out = {}, k;
    raw = raw && typeof raw === 'object' ? raw : {};
    for (k in DEFAULTS) out[k] = typeof raw[k] === 'string' && CHOICES[k].indexOf(raw[k]) !== -1 ? raw[k] : DEFAULTS[k];
    return out;
  }
  var api = { CHOICES: CHOICES, DEFAULTS: DEFAULTS, pick: pick };
  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  if (!root.document) return;

  var stored = null;
  try { stored = JSON.parse(root.localStorage.getItem('relayLook') || 'null'); } catch (e) {}
  var look = pick(stored), h = root.document.documentElement;
  var theme = look.ui_mode === 'system' ? (root.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : look.ui_mode;
  h.dataset.theme = theme; h.dataset.preset = look.ui_preset; h.dataset.mode = look.ui_mode;
  if (FONTS[look.ui_font]) h.style.setProperty('--font', FONTS[look.ui_font]);
})(typeof window !== 'undefined' ? window : globalThis);
