/* The AI models page (#495): each AI task's provider (the Vercel AI Gateway key or the OpenAI key) and model,
   picked from Vercel's own model list with each model's price, free models first. Live transcription picks a
   transcription backend instead, because a module can add its own. Every change goes through the server, which
   normalises it; switching translation moves only the translator, so captions keep running. Everything from the
   server is set as text. */
(function () {
  const $ = function (id) { return document.getElementById(id); };
  const page = $('page-aimodels');
  if (!page) return;

  const h = relayUI.el;
  const request = relayUI.client({ cache: 'no-store', status: true, session: true });
  const showError = relayUI.note('aiError');
  const PROVIDERS = [['vercel', 'Vercel AI Gateway'], ['openai', 'OpenAI key']];
  let state = null;
  let models = [];

  /* A model <select>: the free ones, then the priced ones, then the ones Vercel lists no price for. The saved id is
     always offered, so a model Vercel no longer lists still shows what is saved. */
  function modelSelect(list, saved, strip) {
    const sel = h('select');
    const groups = [['Free', function (m) { return m.free; }],
                    ['Priced', function (m) { return !m.free && m.price !== 'price not listed'; }],
                    ['No price listed', function (m) { return !m.free && m.price === 'price not listed'; }]];
    let found = false;
    groups.forEach(function (g) {
      const picks = list.filter(g[1]);
      if (!picks.length) return;
      const og = h('optgroup', { label: g[0] });
      picks.forEach(function (m) {
        const id = strip ? m.id.slice(strip.length) : m.id;
        if (id === saved) found = true;
        og.appendChild(h('option', { value: id, text: m.name + ' (' + id + ') · ' + m.price }));
      });
      sel.appendChild(og);
    });
    if (!found && saved) sel.insertBefore(h('option', { value: saved, text: saved + ' (saved)' }), sel.firstChild);
    sel.value = saved;
    return sel;
  }

  function ofType(type, filter) {
    return models.filter(function (m) { return m.type === type && (!filter || filter(m)); });
  }
  function openaiOnly(m) { return m.id.indexOf('openai/') === 0; }

  function providerSelect(value) {
    const sel = h('select');
    PROVIDERS.forEach(function (p) {
      const missing = !state.keys[p[0]];
      sel.appendChild(h('option', { value: p[0], text: p[1] + (missing ? ' (key not set)' : '') }));
    });
    sel.value = value;
    return sel;
  }

  async function save(body, done) {
    showError('');
    try {
      await request('POST', '/api/admin/config', body);
      relayUI.say(done, 'Saved.', false, { ms: 2500 });
      await load(false);
    } catch (e) { showError(e.message); }
  }

  function running(t) {
    if (t.warning) return h('p', 'note warn', t.warning);
    if (!t.has_key) return h('p', 'note err', 'No key is saved for this provider, so this task cannot run.');
    return h('p', 'note', 'Runs on ' + (t.running_provider === 'vercel' ? 'the Vercel AI Gateway' : 'the OpenAI key') +
      ' with ' + t.running_model + '.');
  }

  function taskCard(t) {
    const prov = providerSelect(t.provider);
    const vModel = modelSelect(ofType(t.kind), t.model, '');
    const oModel = modelSelect(ofType(t.kind, openaiOnly), t.openai_model, 'openai/');
    const vField = relayUI.field('Model through Vercel', vModel);
    const oField = relayUI.field('Model through the OpenAI key', oModel);
    const said = h('span', 'note');
    function shown() { vField.hidden = prov.value !== 'vercel'; oField.hidden = prov.value !== 'openai'; }
    prov.addEventListener('change', shown);
    shown();
    const body = { ai: { tasks: {} } };
    const btn = relayUI.button('Save', function () {
      body.ai.tasks[t.id] = { provider: prov.value, model: vModel.value, openai_model: oModel.value };
      return save(body, said);
    }, 'primary');
    return h('section', '', h('h3', '', t.label), h('p', 'note', t.note),
      h('div', 'row', relayUI.field('Provider', prov), vField, oField), running(t), h('div', 'ctl', btn, said));
  }

  function transcriptionCard(x) {
    const backend = h('select');
    x.backends.forEach(function (b) {
      const price = b.price_per_hour == null ? '' : ' · $' + b.price_per_hour.toFixed(2) + ' per hour';
      backend.appendChild(h('option', { value: b.id, text: b.label + price + (b.available ? '' : ' (key not set)') }));
    });
    backend.value = x.backend;
    const streaming = ofType('transcription', function (m) { return m.streaming; });
    const scribe = modelSelect(streaming, x.scribe_model, '');
    const oModel = modelSelect(ofType('transcription', openaiOnly), x.openai_model, 'openai/');
    const sField = relayUI.field('Model through Vercel', scribe);
    const oField = relayUI.field('Model through the OpenAI key', oModel);
    function shown() { sField.hidden = backend.value !== 'scribe'; oField.hidden = backend.value !== 'openai'; }
    backend.addEventListener('change', shown);
    shown();
    const said = h('span', 'note');
    const btn = relayUI.button('Save', function () {
      return save({ realtime: { backend: backend.value, scribe_model: scribe.value, transcribe_model: oModel.value } },
        said);
    }, 'primary');
    const status = x.warning ? h('p', 'note warn', x.warning)
      : h('p', 'note', 'Saving restarts the live captions, which takes a second or two.');
    return h('div', '', h('div', 'row', relayUI.field('Who transcribes', backend), sField, oField), status,
      h('div', 'ctl', btn, said));
  }

  function render() {
    $('aiTranscription').replaceChildren(transcriptionCard(state.transcription));
    $('aiTasks').replaceChildren.apply($('aiTasks'), state.tasks.map(taskCard));
  }

  async function load(refresh) {
    try {
      const got = await Promise.all([request('GET', '/api/admin/ai'),
        request('GET', '/api/admin/ai/models' + (refresh ? '?refresh=1' : ''))]);
      state = got[0];
      models = got[1].models || [];
      const free = models.filter(function (m) { return m.free; }).length;
      $('aiCatalogue').textContent = got[1].error ||
        (models.length + ' models from Vercel\'s list, ' + free + ' of them free. Prices are Vercel\'s and are read ' +
         'once an hour.');
      render();
    } catch (e) { showError(e.message); }
  }

  page.addEventListener('relay:tab', function () { load(false); });
  if (!page.hidden) load(false);
})();
