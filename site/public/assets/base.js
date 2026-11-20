// Shared behaviour for the "One view" mockups: word-by-word headline, live clock, mobile menu, repeated content.
(function () {
  // Split [data-words] headings into staggered words (kept readable for screen readers).
  document.querySelectorAll("[data-words]").forEach(function (h) {
    var text = h.textContent.trim();
    h.setAttribute("aria-label", text);
    h.textContent = "";
    text.split(/\s+/).forEach(function (word, i) {
      var s = document.createElement("span");
      s.className = "w";
      s.setAttribute("aria-hidden", "true");
      s.style.animationDelay = (0.15 + i * 0.055).toFixed(3) + "s";
      s.textContent = word;
      h.appendChild(s);
      h.appendChild(document.createTextNode(" "));
    });
  });

  // Caption rolls loop seamlessly: copy each list once, hidden from assistive tech.
  document.querySelectorAll(".roll").forEach(function (r) {
    Array.prototype.slice.call(r.children).forEach(function (p) {
      var c = p.cloneNode(true);
      c.setAttribute("aria-hidden", "true");
      r.appendChild(c);
    });
  });
  // Marquees: duplicate the run for the seamless loop.
  document.querySelectorAll(".mq").forEach(function (m) {
    var run = m.firstElementChild;
    if (!run) return;
    var d = run.cloneNode(true);
    d.className = "dup";
    d.setAttribute("aria-hidden", "true");
    m.appendChild(d);
  });

  document.querySelectorAll(".sheet a").forEach(function (a) {
    a.addEventListener("click", function () { a.closest("details").removeAttribute("open"); });
  });

  var t = 1 * 3600 + 12 * 60 + 44;
  function p(n) { return (n < 10 ? "0" : "") + n; }
  setInterval(function () {
    t += 1;
    var s = p(Math.floor(t / 3600)) + ":" + p(Math.floor(t / 60) % 60) + ":" + p(t % 60);
    document.querySelectorAll("[data-clock]").forEach(function (el) { el.textContent = s; });
  }, 1000);
})();
