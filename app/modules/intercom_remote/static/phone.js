// The remote phone (#277): sign in, open the socket, then audio goes to and from Cloudflare Realtime while Relay,
// not this page, decides which tracks it may pull and when it may talk.
(() => {
  const $ = (id) => document.getElementById(id);
  const KEY = "relay-remote-credential";
  let ws = null, pc = null, mic = null, players = [], blocked = false, iceErrors = [], channels = [], seq = 0, beat = 0, wantOpen = false, retry = 0, pending = {};
  const say = (t, bad) => { $("msg").textContent = t || ""; $("msg").className = bad ? "bad" : ""; };
  const send = (o) => ws && ws.readyState === 1 && ws.send(JSON.stringify(o));
  const get = (k) => { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } };
  const put = (k, v) => { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} };
  // One request at a time, answered by the reply type Relay sends back.
  const ask = (o, reply) => new Promise((res, rej) => {
    pending[reply] = res; pending.error = (m) => rej(new Error(m.text || "Failed"));
    send(o); setTimeout(() => rej(new Error("No answer from Relay")), 30000);
  }).finally(() => { delete pending[reply]; });

  $("login").onsubmit = async (e) => {
    e.preventDefault(); say("Signing in…");
    try {
      const r = await fetch("/intercom/remote/api/sign-in", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: $("u").value, password: $("p").value, name: "Browser" }) });
      const j = await r.json();
      if (!r.ok) return say(j.error || "Could not sign in", true);
      put(KEY, j.credential); $("p").value = ""; connect();
    } catch (err) { say("Could not reach the church.", true); }
  };
  $("out").onclick = () => { wantOpen = false; put(KEY, ""); teardown(); if (ws) ws.close(); show(false); say("Signed out."); };

  function show(on) { $("login").hidden = on; $("app").hidden = !on; }

  function connect() {
    const cred = get(KEY); if (!cred) return show(false);
    wantOpen = true; say("Connecting…");
    ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/intercom/remote/ws");
    ws.onopen = () => send({ type: "hello", credential: cred });
    ws.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === "welcome") { retry = 0; channels = m.channels || []; if (m.credential) put(KEY, m.credential);
        $("prod").textContent = m.production || "Remote crew"; draw(); show(true); say("Connected. Choose channels, then Connect audio."); }
      else if (m.type === "link") { say(m.text, true); }
      else if (m.type === "error" && pending.error) pending.error(m);
      else if (pending[m.type]) pending[m.type](m);
      else if (m.type === "error") say(m.text, true);
      else if (m.type === "channels" || m.type === "config") { channels = m.channels || channels; draw(); }
    };
    ws.onclose = (ev) => {
      teardown();
      if (ev.code === 4401) { put(KEY, ""); show(false); return say("Sign in again.", true); }
      if (!wantOpen) return;
      show(!!get(KEY) && channels.length > 0); say(ev.code === 4503 ? "Lost the link to the church. Reconnecting…" : "Disconnected. Reconnecting…", true);
      setTimeout(connect, Math.min(30000, 1000 * 2 ** retry++));
    };
  }

  function draw() {
    $("chs").textContent = "";
    for (const c of channels) {
      const row = document.createElement("div"); row.className = "ch";
      const name = document.createElement("b"); name.textContent = c.name; row.append(name);
      for (const [key, can, label] of [["listen_selected", c.can_listen, "Listen"], ["talk_selected", c.can_talk, "Talk"]]) {
        if (!can) continue;
        const l = document.createElement("label"), i = document.createElement("input");
        i.type = "checkbox"; i.style.width = "auto"; i.style.margin = "0 6px 0 0"; i.checked = !!c[key];
        i.onchange = () => { c[key] = i.checked; send({ type: "choice", channel: c.id, talk_selected: !!c.talk_selected,
          listen_selected: !!c.listen_selected, volume: 100 }); };
        l.append(i, label); row.append(l);
      }
      $("chs").append(row);
    }
  }

  async function audio() {
    teardown(); say("Connecting audio…");
    const wantTalk = channels.some((c) => c.can_talk && c.talk_selected);
    // The microphone prompt first: Cloudflare drops a session nothing connects to within a few seconds, and a person
    // can take longer than that to answer the prompt.
    if (wantTalk) mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const s = await ask({ type: "cf.open" }, "cf.session");
    if (!s.sessionId) throw new Error("No session");
    // Relay hands over TURN as well as STUN when it can: some mobile networks block Cloudflare's direct UDP.
    const ice = Array.isArray(s.iceServers) && s.iceServers.length ? s.iceServers : [{ urls: "stun:stun.cloudflare.com:3478" }];
    pc = new RTCPeerConnection({ bundlePolicy: "max-bundle", iceServers: ice });
    iceErrors = [];
    pc.onicecandidateerror = (e) => { if (iceErrors.length < 10) iceErrors.push(`${e.errorCode} ${(e.url || "").split("?")[0]} ${e.errorText || ""}`.trim()); };
    pc.onconnectionstatechange = () => { if (pc && pc.connectionState === "connected") send({ type: "cf.connected" }); };
    // One player per channel: a single element would play only the last channel pulled.
    pc.ontrack = (e) => {
      const el = document.createElement("audio"); el.autoplay = true; el.playsInline = true;
      el.srcObject = new MediaStream([e.track]); $("aud").append(el); players.push(el);
      el.play().catch(() => { blocked = true; say("Tap anywhere to hear the channels.", true); });
    };
    if (wantTalk) {
      const track = mic.getAudioTracks()[0]; track.enabled = false;        // open only while the button is held
      const t = pc.addTransceiver(track, { direction: "sendonly" });
      await pc.setLocalDescription(await pc.createOffer());
      const out = await ask({ type: "cf.push", mid: t.mid, sessionDescription: { type: "offer", sdp: pc.localDescription.sdp } }, "cf.pushed");
      await pc.setRemoteDescription(out.sessionDescription);
      // Cloudflare hangs, then refuses, a pull into a session whose connection is not up yet.
      try { await connected(pc); } catch (e) { await diag(pc); throw e; }
      $("ptt").disabled = false;
    }
    if (channels.some((c) => c.can_listen && c.listen_selected)) {
      const out = await ask({ type: "cf.pull" }, "cf.pulled");
      if (out.sessionDescription) {
        await pc.setRemoteDescription(out.sessionDescription);
        await pc.setLocalDescription(await pc.createAnswer());
        await ask({ type: "cf.renegotiate", sessionDescription: { type: "answer", sdp: pc.localDescription.sdp } }, "cf.renegotiated");
      }
    }
    say("Audio connected.");
  }
  function connected(conn) {
    return new Promise((res, rej) => {
      const check = () => {
        if (conn.connectionState === "connected") { done(); res(); }
        else if (["failed", "closed"].includes(conn.connectionState)) { done(); rej(new Error("Could not connect audio.")); }
      };
      const timer = setTimeout(() => { done(); rej(new Error("Audio did not connect. Try again.")); }, 15000);
      const done = () => { clearTimeout(timer); conn.removeEventListener("connectionstatechange", check); };
      conn.addEventListener("connectionstatechange", check); check();
    });
  }
  // When audio does not connect, tell Relay why, for its log: the states and the kinds of candidate each side had.
  async function diag(conn) {
    const o = { type: "cf.diag", connection: conn.connectionState, ice: conn.iceConnectionState,
      gathering: conn.iceGatheringState, signaling: conn.signalingState, local: [], pairs: [], errors: iceErrors.slice(),
      servers: (conn.getConfiguration().iceServers || []).map((x) => [].concat(x.urls).length + " urls" + (x.username ? " with credentials" : "")),
      remote: ((conn.remoteDescription && conn.remoteDescription.sdp) || "").split(/\r?\n/)
        .filter((l) => l.startsWith("a=candidate") || l.startsWith("a=ice-lite")).slice(0, 10) };
    try {
      (await conn.getStats()).forEach((r) => {
        if (r.type === "local-candidate") o.local.push([r.candidateType, r.relayProtocol || r.protocol, (r.address || r.ip || "").includes(":") ? "v6" : "v4"].join(" "));
        if (r.type === "candidate-pair") o.pairs.push([r.state, r.nominated ? "nominated" : "", r.requestsSent || 0, r.responsesReceived || 0].join(" "));
      });
    } catch (e) {}
    o.local = o.local.slice(0, 20); o.pairs = o.pairs.slice(0, 20);
    send(o);
  }
  $("go").onclick = () => audio().catch((e) => say(e.message, true));

  function talking() { return channels.filter((c) => c.can_talk && c.talk_selected).map((c) => c.id); }
  function down() {
    const ids = talking(); if (!ids.length || !mic) return;
    $("ptt").classList.add("on"); mic.getAudioTracks()[0].enabled = true;
    send({ type: "talk.request", channels: ids, seq: ++seq, latched: false });
    beat = setInterval(() => send({ type: "talk.beat" }), 1000);
  }
  function up() {
    clearInterval(beat); $("ptt").classList.remove("on");
    if (mic) mic.getAudioTracks()[0].enabled = false;
    send({ type: "talk.release", channels: talking() });
  }
  const p = $("ptt");
  p.addEventListener("contextmenu", (e) => e.preventDefault());       // a long press must not open a menu or select text
  p.addEventListener("selectstart", (e) => e.preventDefault());
  p.addEventListener("pointerdown", (e) => { e.preventDefault(); down(); });
  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) p.addEventListener(ev, up);

  function teardown() {
    clearInterval(beat); $("ptt").disabled = true; $("ptt").classList.remove("on");
    if (mic) mic.getTracks().forEach((t) => t.stop()); mic = null;
    if (pc) pc.close(); pc = null;
    for (const el of players) { el.srcObject = null; el.remove(); } players = []; blocked = false;
  }
  // A browser that refused to start a player without a tap gets it on the next one.
  document.addEventListener("pointerdown", () => {
    if (!blocked) return; blocked = false;
    for (const el of players) el.play().catch(() => {});
    say("");
  }, true);
  if (get(KEY)) connect();
})();
