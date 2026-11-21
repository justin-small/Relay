/* The No sound? tab of the crew page (#263): the shared walk (relayUI.noSoundWalk) asked of the crew route. */
(function () {
  'use strict';
  const host = document.getElementById('nsWalk');
  if (host) relayUI.noSoundWalk(host, '/crew/api/nosound');
})();
