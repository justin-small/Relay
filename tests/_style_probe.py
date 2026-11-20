"""The computed-style check of #463, run in the browser on an operator page (tests/test_browser.py, and the demo
sweep in docs/ui-audit/demo). It reads what the browser drew, not the stylesheets, so a page that styles a control
its own way is caught however it did it.

On the page that is showing:
* every button is 34px (regular), 28px (.sm) or 32px (icon) tall, with a label of 12, 13 or 14px;
* every text field and select is 34px or 28px tall, with text of 12, 13 or 14px;
* text is drawn only at sizes on the type scale in tokens.css, and body text (not headings) at no more than 4 sizes.

What is left out is listed in EXEMPT and written down in docs/ui-audit/standardization-spec.md: shell controls with
their own fixed size (the menu, the account row, the search box), and the things that draw the operator's own looks
(the board and cue canvases, the overlay preset thumbnails).
"""

EXEMPT = ", ".join([
    # Shell controls with their own fixed size.
    ".user", ".nav a", ".nav .nb", ".sel", ".tg button", ".ib", ".search", ".qc .go", ".qc .inb", ".mi", ".sort",
    ".plink", "button.link", ".switch *",
    # What draws the operator's own looks: overlay presets and stage, board and cue canvases, slides, the network map.
    ".ovpreset", ".ovpreset *", ".ovstage *", ".cuebox *", ".rl-canvas *", ".rl-mini *", ".rl-pal *", ".nmframe *",
    ".ppcue", ".ppcue *", "[data-style-exempt]", "[data-style-exempt] *",
    # Live-control and phone touch targets: ProPresenter's big transport, the crew page's tabs and intercom.
    "button.big", ".cr-tabs *", ".cr-confirm", ".cr-alerts button", "#ic .tog", "#icTalk",
])

PROBE = r"""(exempt) => {
  const BTN_H = [28, 32, 34], FIELD_H = [28, 34], CTL_FS = [12, 13, 14];
  const SCALE = [11, 12, 13, 14, 15, 18, 22, 24];
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !e.closest('[hidden]'); };
  const name = e => { let s = e.tagName.toLowerCase(); if (e.id) return s + '#' + e.id;
    if (e.classList.length) s += '.' + [...e.classList].slice(0, 2).join('.');
    const t = (e.textContent || e.value || '').trim().slice(0, 24); return t ? s + ' "' + t + '"' : s; };
  const root = document.querySelector('.page:not([hidden])') || document.querySelector('main') || document.body;
  const bad = [];
  for (const b of root.querySelectorAll('button, .btn, a.btnlink')) {
    if (!vis(b) || b.matches(exempt)) continue;
    const h = Math.round(b.getBoundingClientRect().height), fs = parseFloat(getComputedStyle(b).fontSize);
    if (!BTN_H.includes(h)) bad.push('button ' + name(b) + ' is ' + h + 'px tall');
    if (!CTL_FS.includes(fs)) bad.push('button ' + name(b) + ' label is ' + fs + 'px');
  }
  for (const i of root.querySelectorAll('input, select')) {
    if (!vis(i) || i.matches(exempt)) continue;
    if (/^(checkbox|radio|range|color|file|hidden|submit|button|image)$/.test(i.type)) continue;
    const h = Math.round(i.getBoundingClientRect().height), fs = parseFloat(getComputedStyle(i).fontSize);
    if (!FIELD_H.includes(h)) bad.push('field ' + name(i) + ' is ' + h + 'px tall');
    if (!CTL_FS.includes(fs)) bad.push('field ' + name(i) + ' text is ' + fs + 'px');
  }
  const body = new Set();
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const done = new Set();
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const e = n.parentElement;
    if (!e || done.has(e) || !n.textContent.trim() || !vis(e) || e.matches(exempt)) continue;
    done.add(e);
    if (e.closest('svg, canvas, iframe, code, kbd, pre, .mono, textarea, input, select, option')) continue;
    const fs = Math.round(parseFloat(getComputedStyle(e).fontSize) * 10) / 10;
    if (!SCALE.includes(fs)) { bad.push('text ' + name(e) + ' is ' + fs + 'px'); continue; }
    const heading = e.closest('h1, h2, h3, h4, h5, summary, legend, .title, .pname, .fwname, .v, [data-figure]');
    if (!heading && fs <= 14) body.add(fs);
  }
  if (body.size > 4) bad.push('body text in ' + body.size + ' sizes: ' + [...body].sort().join(', '));
  return bad.slice(0, 40);
}"""
