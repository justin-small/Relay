/* Sample data for the website mockup: stands in for the "rundown" stream so the real rundown.js and rundown.css draw
   the board exactly as /screen does. The clock is held at 10:12 on a Sunday. Names and songs are made up. */
(function () {
  const d = new Date(); d.setHours(10, 12, 0, 0);
  const T0 = d.getTime() / 1000, start = Date.now() / 1000;
  const at = (h, m) => { const x = new Date(d); x.setHours(h, m, 0, 0); return x.getTime() / 1000; };
  const band = (ids) => ids.map((m) => ({ mic: String(m) }));
  const items = [
    { title: 'Walk-in countdown', kind: 'media', state: 'done', planned: at(9, 55), actual: at(9, 55), length: 300 },
    { title: 'Welcome', kind: 'talk', state: 'done', planned: at(10, 0), actual: at(10, 0), length: 180 },
    { title: 'Worship', kind: 'header', planned: at(10, 3) },
    { title: 'Morning Light', kind: 'song', state: 'done', planned: at(10, 3), actual: at(10, 4), length: 300, song: { key: 'G', leader: 'Maya Ortiz', mics: band([1, 2, 3, 4, 5]) } },
    { title: 'Here Again', kind: 'song', state: 'current', planned: at(10, 8), actual: at(10, 9), length: 300, song: { key: 'D', leader: 'Maya Ortiz', mics: band([1, 2, 3, 4, 5]) } },
    { title: 'Open Hands', kind: 'song', state: 'next', planned: at(10, 13), projected: at(10, 14), length: 360, song: { key: 'A', leader: 'Daniel Kim', mics: band([1, 2, 4, 5]) } },
    { title: 'Announcements', kind: 'talk', planned: at(10, 19), projected: at(10, 20), length: 240 },
    { title: 'Message', kind: 'talk', planned: at(10, 23), projected: at(10, 24), length: 1920 },
    { title: 'Altar call', kind: 'song', planned: at(10, 55), projected: at(10, 56), length: 360, song: { key: 'D', leader: 'Maya Ortiz', mics: band([1, 2, 4]) } },
    { title: 'Dismissal', kind: 'talk', planned: at(11, 1), projected: at(11, 2), length: 120 },
  ];
  const person = (name) => ({ name });
  const mics = [
    { mic: '1', position: 'Worship leader', person: person('Maya Ortiz'), on: true },
    { mic: '2', position: 'Vocals', person: person('Rosa Tran'), on: true },
    { mic: '3', position: 'Vocals', person: person('James Adeyemi'), on: true },
    { mic: '4', position: 'Keys', person: person('Daniel Kim'), on: true },
    { mic: '5', position: 'Acoustic guitar', person: person('Lena Novak'), on: true },
    { mic: '6', position: 'Vocals', person: null, on: false },
    { mic: '7', position: 'Host', person: person('Eli Park'), on: false },
    { mic: '8', position: 'Pastor', person: person('Grace Wu'), on: false },
  ];
  const people = mics.filter((c) => c.person).map((c) => ({ name: c.person.name, team: 'Worship', position: c.position, status: 'confirmed', mic: c.mic }))
    .concat([
      { name: 'Sam Brooks', team: 'Production', position: 'Producer', status: 'confirmed' },
      { name: 'Ana Ruiz', team: 'Production', position: 'Camera 1', status: 'confirmed' },
      { name: 'Tom Lee', team: 'Production', position: 'Camera 2', status: 'pending' },
      { name: '', team: 'Production', position: 'Camera 3', status: 'unfilled' },
      { name: 'Priya Shah', team: 'Production', position: 'Lighting', status: 'confirmed' },
      { name: 'Mark Diaz', team: 'Production', position: 'Slides', status: 'confirmed' },
      { name: 'Ruth Allen', team: 'Hospitality', position: 'Greeter', status: 'confirmed' },
      { name: 'Ben Ortiz', team: 'Hospitality', position: 'Greeter', status: 'confirmed' },
    ]);
  function message() {
    const now = T0 + (Date.now() / 1000 - start);
    return {
      type: 'rundown', now, live: true, plan: { title: 'Sunday Gathering' }, time: { starts_at: at(10, 0) },
      header: { current: 'Here Again', next: 'Open Hands', kind: 'song', song: { key: 'D', leader: 'Maya Ortiz' },
                item_started_at: at(10, 9), item_length: 300, planned_end: at(11, 3), projected_end: at(11, 4), over_s: 60 },
      items, mics, people,
      notes: { item: 'Message', planned: at(10, 23), projected: at(10, 24),
               text: 'Remain in me · John 15:1–8\n\n1. The vine and the branches. We were never meant to carry this alone.\n\n2. Pruning is not punishment. What is cut back grows back stronger.\n\n3. Fruit that lasts. Love one another as I have loved you.\n\nResponse: invite people forward during the altar call.' },
    };
  }
  window.EventSource = function () {
    const es = this; es.readyState = 1; es.close = function () {};
    setTimeout(function () { es.onmessage && es.onmessage({ data: JSON.stringify(message()) }); }, 30);
  };
  window.EventSource.CLOSED = 2;
})();
