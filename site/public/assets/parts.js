// Content shared by the three mockups, so each page only carries its own visual world.
// Each <div data-part="name"> is replaced by the matching block. Loaded before base.js.
(function () {
  var MARK = '<g transform="translate(-15.3 -14.55) scale(1.4545)"><rect x="11.5" y="14" width="9" height="36.4" rx="4.5" fill="#f4f4f5"/><path fill-rule="evenodd" fill="#4ade80" d="M33.5 14H44.5A9 9 0 0 1 53.5 23V30A9 9 0 0 1 49 37.8L52.6 48.2Q53.6 51.4 50.4 50.2L37 39H33.5A9 9 0 0 1 24.5 30V23A9 9 0 0 1 33.5 14ZM32.5 20.8H45.5A2 2 0 0 1 47.5 22.8V22.8A2 2 0 0 1 45.5 24.8H32.5A2 2 0 0 1 30.5 22.8V22.8A2 2 0 0 1 32.5 20.8ZM32.5 28.3H39.0A2 2 0 0 1 41.0 30.3V30.3A2 2 0 0 1 39.0 32.3H32.5A2 2 0 0 1 30.5 30.3V30.3A2 2 0 0 1 32.5 28.3Z"/></g>';
  var ARROW = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 11.5l7-7M5.5 4.5h6v6"/></svg>';
  var DOWN = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3.5v9M4 8.5l4 4 4-4"/></svg>';
  window.RELAY = { MARK: MARK, ARROW: ARROW, DOWN: DOWN };

  function lines(side) {
    var out = "";
    for (var i = 0; i < 8; i++) {
      var y = (6.25 + i * 12.5).toFixed(2);
      var d = side === "in" ? "M0 " + y + "C50 " + y + " 50 50 100 50" : "M0 50C50 50 50 " + y + " 100 " + y;
      out += '<path d="' + d + '"/><path class="fl" d="' + d + '" style="animation-delay:-' + (i * 0.19).toFixed(2) + 's"/>';
    }
    return '<svg class="hw" viewBox="0 0 100 100" preserveAspectRatio="none">' + out + "</svg>";
  }
  function chips(list) {
    return '<ul class="hc">' + list.map(function (c) { return '<li><span class="chip"><span>' + c[0] + "</span><small>" + c[1] + "</small></span></li>"; }).join("") + "</ul>";
  }
  function roll(list, lang) {
    return '<div' + (lang ? ' lang="' + lang + '"' : "") + '><div class="feed"><div class="roll">' + list.map(function (s) { return "<p>" + s + "</p>"; }).join("") + "</div></div></div>";
  }
  var EN = ["Good morning, and welcome.", "We are so glad you are here with us today.", "Please stand with us as we sing.", "Let us pray together.", "Please be seated."];
  var ES = ["Buenos días y bienvenidos.", "Nos alegra mucho que estén aquí con nosotros hoy.", "Por favor, pónganse de pie mientras cantamos.", "Oremos juntos.", "Pueden sentarse."];

  var parts = {
    logo: '<svg role="img" aria-label="Relay" xmlns="http://www.w3.org/2000/svg" width="99" height="29" viewBox="0 0 219.3 64">' + MARK + '<path transform="translate(84.29 19.6) scale(0.62)" d="M0 40V0M0 18A18 18 0 0 1 18 0M33 20H73A20 20 0 1 0 67.14 34.14M92 -24V40M151 20A20 20 0 1 0 151 20.01M151 0V40M170 0V20A20 20 0 0 0 210 20M210 0V44A16 16 0 0 1 194 60H186" fill="none" stroke="#f4f4f5" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    mark: '<svg aria-hidden="true" viewBox="0 0 64 64" width="72" height="72">' + MARK + "</svg>",

    glass:
      '<div class="gl" role="img" aria-label="Relay\'s status view: cameras, stream, slides, service plan, lighting, intercom, network and captions on one screen"><div aria-hidden="true" style="display:contents">' +
      '<div class="gbar"><b>Sunday 10:00</b><span>Item 3 of 9 · Worship set</span><span class="onair">On air <span data-clock>01:12:44</span></span></div><div class="gg">' +
      '<div class="t g5"><h4>Cameras <i>ATEM</i></h4><div class="cams"><span class="cam pgm k1">1</span><span class="cam pvw k2">2</span><span class="cam k3">3</span><span class="cam">4</span></div><div class="row"><span>Red is on air, green is next</span></div></div>' +
      '<div class="t g7"><h4>Stream <i>OBS · YouTube</i></h4><div class="row"><span><span class="ok"></span>OBS streaming and recording</span><b>0 dropped</b></div><div class="row"><span><span class="ok"></span>YouTube is live</span><b>214 watching</b></div><div class="row"><span><span class="ok"></span>Captions in the stream</span><b>English</b></div></div>' +
      '<div class="t g7"><h4>Slides <i>ProPresenter</i></h4><p class="big">We are so glad you are here with us today.</p><div class="row"><span>Next: Announcements</span><b>Slide 14 of 32</b></div></div>' +
      '<div class="t g5"><h4>Service <i>Planning Center</i></h4><div class="row"><span>Worship set</span><span class="live">LIVE</span></div><div class="row"><span>Announcements</span><b>3:00</b></div><div class="row"><span>Message</span><b>35:00</b></div></div>' +
      '<div class="t g4"><h4>Lighting <i>QLC+</i></h4><div class="cues"><span class="cue">Walk-in</span><span class="cue on">Worship</span><span class="cue">Message</span></div></div>' +
      '<div class="t g4"><h4>Intercom <i>7 on</i></h4><div class="row"><span><span class="bars"><i></i><i></i><i></i><i></i></span>Sam on Production</span></div><div class="row"><span>Band</span><b>3 listening</b></div></div>' +
      '<div class="t g4"><h4>Network <i>UniFi</i></h4><div class="row"><span><span class="ok"></span>Internet</span><b>18 ms</b></div><div class="row"><span><span class="ok"></span>Dante and NDI</span><b>Healthy</b></div></div>' +
      '<div class="t g12"><h4>Captions <i>English · Español · 38 reading</i></h4><div class="cap">' + roll(EN) + roll(ES, "es") + "</div></div>" +
      "</div></div></div>",

    hub:
      '<div class="hub">' +
      chips([["Soundboard feed", "audio in"], ["ProPresenter", "slides, timers"], ["Planning Center", "today's plan"], ["Blackmagic ATEM", "tally, cuts"], ["OBS Studio", "stream, scenes"], ["YouTube", "live status"], ["Lighting software", "cues"], ["UniFi network", "health"]]) +
      '<div class="hwr" aria-hidden="true">' + lines("in") + '</div><div class="hm">' + '<svg aria-hidden="true" viewBox="0 0 64 64" width="72" height="72">' + MARK + "</svg>" + '</div><div class="hwr" aria-hidden="true">' + lines("out") + "</div>" +
      chips([["Operator panel", "the booth"], ["Crew phones", "plan, chat"], ["Intercom", "talk"], ["Caption panels", "walls, stage"], ["Camera screens", "tally"], ["Stream overlays", "captions"], ["Lobby TVs", "signage"], ["Visitors' phones", "12 languages"]]) +
      "</div>",

    systems: '<div class="mqw"><div class="mq" style="animation-duration:54s"><span><span>ProPresenter</span><span>Planning Center</span><span>Blackmagic ATEM</span><span>OBS Studio</span><span>YouTube</span><span>REAPER</span><span>Tally Arbiter</span><span>QLC+</span><span>Lightkey</span><span>Vista by Chroma-Q</span><span>ShowXpress</span><span>UniFi</span><span>NDI</span><span>Bitfocus Companion</span><span>Open Sound Meter</span><span>OSC</span></span></div></div>',

    langs: '<div class="mqw"><div class="mq"><span><span lang="es">Español</span><span lang="pt">Português</span><span lang="fr">Français</span><span lang="de">Deutsch</span><span lang="it">Italiano</span><span lang="ru">Русский</span><span lang="zh">中文</span><span lang="ja">日本語</span><span lang="ko">한국어</span><span lang="hi">हिन्दी</span><span lang="id">Bahasa Indonesia</span><span lang="vi">Tiếng Việt</span></span></div></div>',

    welcome:
      '<p class="wl rv">Welcome. We are glad you are here.</p>' +
      '<p class="wl rv" lang="es" style="color:#c9cbd1">Bienvenidos. Nos alegra que estén aquí.</p>' +
      '<p class="wl rv" lang="pt" style="color:#a1a1aa">Bem-vindos. Que bom que vocês estão aqui.</p>' +
      '<p class="wl rv" lang="fr" style="color:#85858d">Bienvenue. Nous sommes heureux de vous voir.</p>' +
      '<p class="wl rv" lang="ko" style="color:#6e6e76;letter-spacing:-0.02em">환영합니다. 함께해 주셔서 기쁩니다.</p>' +
      '<p class="wl rv" lang="zh" style="color:#5b5b63;letter-spacing:0">欢迎。很高兴你们来到这里。</p>',

    // Integrations: available now vs planned. "Now" = a module in app/modules or core today; "Planned" = an open issue.
    switchboard: (function () {
      var groups = [
        ["Video and stream", [["Blackmagic ATEM", 1], ["OBS Studio", 1], ["YouTube, live status and scheduled streams", 1], ["Other switchers, through Tally Arbiter", 1], ["NDI sources", 1], ["Facebook Live", 0]]],
        ["Words and slides", [["Captions and translation", 1], ["ProPresenter", 1], ["Caption panels", 1], ["Stream overlays", 1], ["Spoken translation", 0]]],
        ["Plan and people", [["Planning Center Services", 1], ["Check-Ins, for parent paging", 1], ["Crew pages", 1], ["Intercom, in the building and off site", 1], ["iOS and Android crew apps", 0]]],
        ["Lighting and sound", [["QLC+, Lightkey, Vista, ShowXpress", 1], ["REAPER recording", 1], ["Open Sound Meter levels", 1], ["Soundboard control", 0], ["Wireless mic monitoring", 0], ["Q-SYS", 0]]],
        ["Building and network", [["Signage on any TV", 1], ["UniFi network, read only", 1], ["Remote access for viewers", 1], ["More than one campus", 0]]],
        ["Out to other tools", [["Alert webhooks and OSC", 1], ["Bitfocus Companion", 1], ["Command Center, Relay driven by chat", 0]]]
      ];
      return '<div class="sb">' + groups.map(function (g) {
        return '<div class="sbg rv"><p class="grp">' + g[0] + "</p>" + g[1].map(function (m) {
          return '<div class="mod"><span' + (m[1] ? "" : ' class="dim"') + ">" + m[0] + "</span>" + (m[1] ? '<span class="tg" aria-label="Available now"><i></i></span>' : '<span class="soon">Planned</span>') + "</div>";
        }).join("") + "</div>";
      }).join("") + "</div>";
    })()
  };

  document.querySelectorAll("[data-part]").forEach(function (el) {
    var html = parts[el.getAttribute("data-part")];
    if (html) el.outerHTML = html;
  });
})();
