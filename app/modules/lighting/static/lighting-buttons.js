/* Draws one cue page's buttons (#379). One drawing for /screen and the admin preview, so what the operator designs
   is what the tablet shows. window.LightingButtons.draw(root, page, onPress) -> {buttons: {cueId: element}}. */
(function () {
  function place(b, c, free) {
    b.dataset.shape = c.shape;
    b.style.background = c.color;
    b.style.color = c.text_color;
    if (free) {
      b.style.left = c.x + '%'; b.style.top = c.y + '%'; b.style.width = c.fw + '%'; b.style.height = c.fh + '%';
    } else {
      b.style.gridColumn = 'span ' + c.w; b.style.gridRow = 'span ' + c.h;
    }
  }
  function draw(root, page, onPress) {
    root.textContent = '';
    const buttons = {};
    page.cues.forEach(function (c) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ltcue';
      b.textContent = c.label || c.name;
      place(b, c, page.free);
      if (onPress) b.onclick = function () { onPress(c, b); };
      root.appendChild(b);
      buttons[c.id] = b;
    });
    return { buttons: buttons, place: place };
  }
  window.LightingButtons = { draw: draw, place: place };
})();
