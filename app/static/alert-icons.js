/* The alert buttons' icons (#121): one per name in app/alerts.py ICONS. Line drawings on a 24 x 24 grid, drawn in
   the text colour, for the /alert page and the admin page's picker. */
(function () {
  'use strict';
  const ICONS = {
    bell: ['Bell', '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5h4"/>'],
    mic: ['Microphone', '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6"/>'],
    headphones: ['Headphones', '<path d="M4 16v-4a8 8 0 0 1 16 0v4"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/>'],
    keys: ['Keys', '<rect x="3" y="6" width="18" height="12" rx="1.5"/><path d="M8 6v7M12 6v7M16 6v7M8 13v5M12 13v5M16 13v5"/>'],
    drums: ['Drums', '<ellipse cx="12" cy="10" rx="8" ry="3"/><path d="M4 10v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7M6 3l4 5M18 3l-4 5"/>'],
    guitar: ['Guitar', '<circle cx="8.5" cy="15.5" r="4.5"/><path d="M11.5 12.5L19 5M17 3l4 4"/><circle cx="8.5" cy="15.5" r="1"/>'],
    person: ['Person', '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>'],
    people: ['Group', '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 19a6 6 0 0 1 12 0M15.5 19a5 5 0 0 1 6.5-4.6"/>'],
    video: ['Video', '<rect x="3" y="7" width="12" height="10" rx="2"/><path d="M15 11l6-3v8l-6-3z"/>'],
    sliders: ['Mixer', '<path d="M6 4v16M12 4v16M18 4v16"/><rect x="4" y="12" width="4" height="3" rx="1"/><rect x="10" y="7" width="4" height="3" rx="1"/><rect x="16" y="14" width="4" height="3" rx="1"/>'],
  };

  function svg(name) {
    const icon = ICONS[name] || ICONS.bell;
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true">' + icon[1] + '</svg>';
  }

  window.RelayAlertIcons = {
    names: Object.keys(ICONS),
    label: function (name) { return (ICONS[name] || ICONS.bell)[0]; },
    svg: svg,
  };
})();
