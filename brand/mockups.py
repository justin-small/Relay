#!/usr/bin/env python3
"""Write brand/mockups.html: the three logo concepts shown where Relay uses a logo."""
import pathlib
import marks as M

KEYS = ["a", "a1", "a2", "a3"]
LABEL = {"a1": "A1 · Monoline", "a2": "A2 · Joined", "a3": "A3 · Speaker", "a": "A · Chosen"}

def each(fn):
    return "".join(f'<span class="v v-{c}">{fn(c)}</span>' for c in KEYS)

lock = lambda dark, tiled, h: each(lambda c: M.lockup(c, dark, tiled, h))
tile = lambda s, **k: each(lambda c: M.tile(c, s, small=s <= 32, **k))
NOTES = {
 "a1": "The same R drawn as an outline, in the stroke the wordmark is written in, so mark and name read as one hand. Lightest of the three; at 32 pixels and under the stroke thickens and the caption line drops out.",
 "a2": "The upright and the bubble touch, so it reads first as a solid capital R and second as a bubble. Boldest, and the same drawing works at every size.",
 "a3": "The upright becomes a person and the bubble shows three dots: someone speaking, words arriving. Most literal about what Relay does; the R is a little less obvious.",
 "a": "The chosen mark: a separate upright and a caption bubble with two lines, together reading as R.",
}
SHOW = ", ".join(f'[data-c="{c}"] .v-{c}' for c in KEYS)
BUTTONS = "".join(f'<button id="pick-{c}" data-c="{c}" aria-pressed="{str(c == KEYS[0]).lower()}">{LABEL[c]}</button>' for c in KEYS)
ladder = "".join(f'<figure>{tile(s)}<figcaption>{s}</figcaption></figure>' for s in (16, 24, 32, 48, 64, 128))
variants = (
 f'<figure class="sw dk">{each(lambda c: M.glyph(c, 56, M.FG, M.ACC))}<figcaption>On dark</figcaption></figure>'
 f'<figure class="sw lt">{each(lambda c: M.glyph(c, 56, M.INK, M.ACC_L))}<figcaption>On light</figcaption></figure>'
 f'<figure class="sw dk">{each(lambda c: M.glyph(c, 56, "#fff", "#fff"))}<figcaption>One colour, white</figcaption></figure>'
 f'<figure class="sw lt">{each(lambda c: M.glyph(c, 56, "#000", "#000"))}<figcaption>One colour, black</figcaption></figure>'
 f'<figure class="sw gr">{each(lambda c: M.glyph(c, 56, "#06210f", "#06210f"))}<figcaption>On the live green</figcaption></figure>'
 f'<figure class="sw dk wide">{lock(True, False, 44)}<figcaption>Lockup, dark</figcaption></figure>'
 f'<figure class="sw lt wide">{lock(False, False, 44)}<figcaption>Lockup, light</figcaption></figure>'
)
ph = '<i class="app"></i>' * 3
page = f'''<title>Relay Logo Mockups</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
/* Layout: a concept switcher on top, then a wall of real Relay surfaces, each showing the chosen concept. */
:root {{ --bg:#f2f3f1; --fg:#17191c; --dim:#5d636b; --line:#d9dcd7; --card:#ffffff; --accent:#15803d; --accent-ink:#ffffff;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,system-ui,sans-serif; --mono:ui-monospace,SFMono-Regular,Menlo,monospace; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#0d0e10; --fg:#eceef0; --dim:#8d939c; --line:#24272c; --card:#15171a; --accent:#4ade80; --accent-ink:#06210f; color-scheme:dark }} }}
:root[data-theme="dark"] {{ --bg:#0d0e10; --fg:#eceef0; --dim:#8d939c; --line:#24272c; --card:#15171a; --accent:#4ade80; --accent-ink:#06210f; color-scheme:dark }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 var(--sans); padding-block:36px 64px;
  padding-left:max(16px, env(safe-area-inset-left, 0px)); padding-right:max(16px, env(safe-area-inset-right, 0px)); }}
main {{ max-width:1120px; margin:0 auto; display:flex; flex-direction:column; gap:40px }}
h1 {{ font-size:30px; line-height:1.15; margin:0; letter-spacing:-.02em; text-wrap:balance }}
h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.09em; color:var(--dim); margin:0; font-weight:600 }}
p {{ margin:0; max-width:65ch }} .dim {{ color:var(--dim) }}
header {{ display:flex; flex-direction:column; gap:16px }}
.pick {{ display:flex; flex-wrap:wrap; gap:8px }}
.pick button {{ font:600 14px var(--sans); color:var(--fg); background:var(--card); border:1px solid var(--line); border-radius:999px; padding:9px 16px; cursor:pointer }}
.pick button[aria-pressed="true"] {{ background:var(--accent); border-color:var(--accent); color:var(--accent-ink) }}
.pick button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px }}
.v {{ display:none }} {SHOW} {{ display:contents }}
section {{ display:flex; flex-direction:column; gap:16px }}
.wall {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:20px; align-items:start }}
.scene {{ display:flex; flex-direction:column; gap:10px; min-width:0; margin:0 }}
.scene.wide {{ grid-column:1 / -1 }}
figcaption {{ font-size:13px; color:var(--dim) }}
svg {{ display:block; max-width:100%; height:auto; flex:none }}
/* The surfaces below are pictures of Relay itself, so their colours are Relay's, not this page's. */
.shot {{ border-radius:12px; overflow:hidden; border:1px solid var(--line); font-family:var(--sans) }}
.tabs {{ display:flex; flex-direction:column }}
.strip {{ display:flex; gap:6px; padding:8px 10px 0; align-items:flex-end }}
.strip.dk {{ background:#1d1f23 }} .strip.lt {{ background:#dfe1e5 }}
.tab {{ display:flex; align-items:center; gap:8px; padding:8px 12px; border-radius:9px 9px 0 0; font-size:12px; min-width:0; white-space:nowrap; overflow:hidden }}
.strip.dk .tab {{ color:#9aa0a8 }} .strip.dk .tab.on {{ background:#35373c; color:#f1f3f4 }}
.strip.lt .tab {{ color:#5f6368 }} .strip.lt .tab.on {{ background:#fff; color:#202124 }}
.admin {{ background:#000; color:#f4f4f5 }}
.bar {{ display:flex; align-items:center; gap:12px; padding:12px 16px; background:#050506; border-bottom:1px solid #1f2024; flex-wrap:wrap }}
.bar .sp {{ flex:1 }} .pill {{ font-size:12px; color:#4ade80; border:1px solid #1f3a2a; border-radius:999px; padding:3px 10px }}
.btn {{ font-size:12px; background:#17181c; border-radius:8px; padding:6px 12px; color:#f4f4f5 }}
.rows {{ padding:16px; display:grid; gap:10px }} .rows i {{ height:38px; border-radius:10px; background:#0a0b0d; border:1px solid #1f2024 }}
.phones {{ display:flex; gap:20px; justify-content:center; flex-wrap:wrap; background:var(--card); padding:24px 16px; border-radius:12px; border:1px solid var(--line) }}
.phone {{ width:232px; max-width:100%; aspect-ratio:9/18.5; border-radius:34px; border:7px solid #2a2c31; background:#000; color:#f4f4f5; padding:26px 16px 16px; display:flex; flex-direction:column; gap:12px; overflow:hidden }}
.phone b {{ font-size:19px; margin-top:8px }} .phone small {{ color:#6b7280; font-size:11px; line-height:1.35 }}
.choice {{ background:#0a0b0d; border:1px solid #1f2024; border-radius:12px; padding:10px 12px; font-size:13px; font-weight:600; display:grid }}
.choice span {{ font-weight:400; font-size:11px; color:#6b7280 }}
.phone.home {{ background:linear-gradient(160deg,#27405c,#132032); padding-top:38px }}
.grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px 10px; justify-items:center; font-size:9.5px; text-align:center }}
.app {{ width:40px; height:40px; border-radius:10px; background:rgba(255,255,255,.22); display:block }}
.grid svg {{ border-radius:10px; margin-bottom:3px }}
.panel {{ background:#1b1c1f; padding:14px; border-radius:16px }}
.panel .scr {{ aspect-ratio:800/480; background:#000; border-radius:4px; display:grid; place-items:center; align-content:center; gap:5%; color:#6b7280; font-size:clamp(10px,1.6vw,15px) }}
.panel .scr svg {{ width:42% }}
.cover {{ background:#fff; color:#18181b; aspect-ratio:210/297; padding:12% 10%; display:flex; flex-direction:column; gap:8px }}
.cover strong {{ font-size:28px; letter-spacing:-.02em; margin-top:auto; line-height:1.1 }} .cover span {{ color:#52525b; font-size:13px }}
.cover hr {{ width:44px; height:4px; border:0; background:#15803d; margin:10px 0 0 }}
.login {{ background:#000; color:#f4f4f5; padding:36px 20px; display:grid; justify-items:center; gap:14px }}
.login .field {{ width:min(240px,100%); height:38px; border-radius:10px; background:#0a0b0d; border:1px solid #1f2024 }}
.login .go {{ width:min(240px,100%); text-align:center; padding:9px; border-radius:10px; background:#4ade80; color:#06210f; font-weight:600; font-size:13px }}
.social {{ background:#000; aspect-ratio:1280/640; display:grid; place-items:center; align-content:center; gap:7%; color:#8b8f99; font-size:clamp(11px,2vw,22px) }}
.social svg {{ width:40% }}
.row {{ display:flex; flex-wrap:wrap; gap:14px; align-items:flex-end }}
.row figure {{ margin:0; display:flex; flex-direction:column; gap:8px; align-items:center }}
.ladder {{ background:#000; border-radius:12px; padding:22px; border:1px solid var(--line) }} .ladder.lt {{ background:#fff }}
.ladder figcaption {{ font:12px var(--mono); color:#8d939c }}
.sw {{ border-radius:12px; padding:18px 22px; border:1px solid var(--line); min-width:112px }}
.sw.dk {{ background:#000 }} .sw.lt {{ background:#fff }} .sw.gr {{ background:#4ade80 }} .sw.wide {{ min-width:220px }}
.sw.dk figcaption {{ color:#8d939c }} .sw.lt figcaption {{ color:#52525b }} .sw.gr figcaption {{ color:#06210f }}
table {{ border-collapse:collapse; width:100%; font-size:14px }} .scroll {{ overflow-x:auto }}
td, th {{ text-align:left; padding:8px 14px 8px 0; border-bottom:1px solid var(--line); vertical-align:top }}
th {{ font-size:12px; color:var(--dim); font-weight:600 }} code {{ font:13px var(--mono) }}
</style>
<main data-c="a" id="root">
<header>
  <h1>Relay logo: the Bubble R</h1>
  <p class="dim">Pick a version and every surface below redraws with it. Colours come from the app as it is today: black, off-white, and the green that already means live.</p>
  <div class="pick" role="group" aria-label="Concept">
    {BUTTONS}
  </div>
  {"".join(f'<p class="v v-{c}"><span>{NOTES[c]}</span></p>' for c in KEYS)}
</header>

<section><h2>In the software</h2><div class="wall">
  <figure class="scene"><div class="shot tabs">
    <div class="strip dk"><span class="tab on">{tile(16)}Relay · Operator</span><span class="tab">{tile(16)}Live Translation</span></div>
    <div class="strip lt"><span class="tab on">{tile(16)}Relay · Operator</span><span class="tab">{tile(16)}Live Translation</span></div>
  </div><figcaption>Browser tab, 16 px, dark and light browsers</figcaption></figure>

  <figure class="scene"><div class="shot admin">
    <div class="bar">{lock(True, False, 26)}<span class="dim" style="color:#6b7280;font-size:13px">Operator</span><span class="sp"></span><span class="pill">● Live</span><span class="btn">Sign out</span></div>
    <div class="rows"><i></i><i></i><i></i></div>
  </div><figcaption>Operator panel top bar</figcaption></figure>

  <figure class="scene"><div class="shot login">{tile(60)}<strong>Operator sign in</strong><span class="field"></span><span class="go">Sign in</span></div>
  <figcaption>Operator sign-in page</figcaption></figure>

  <figure class="scene wide"><div class="phones">
    <div class="phone">{lock(True, False, 24)}<b>Live Captions</b><small>Pick a view. Nothing to install.</small>
      <div class="choice">Translation<span>Read along in your language</span></div><div class="choice">Transcription<span>What is being said</span></div><div class="choice">Both<span>Side by side</span></div></div>
    <div class="phone home"><div class="grid">{ph}<span>{tile(40, radius=0)}Relay</span>{ph}<i class="app"></i>{ph}</div></div>
  </div><figcaption>The viewer link on a phone, and saved to the home screen</figcaption></figure>

  <figure class="scene wide"><div class="panel"><div class="scr">{lock(True, False, 96)}<span>Looking for Relay on the network</span></div></div>
  <figcaption>Display panel start-up screen, 800 by 480</figcaption></figure>

  <figure class="scene"><div class="shot cover">{lock(False, False, 34)}<strong>User Guide</strong><span>Running Relay at a venue</span><hr></div>
  <figcaption>User guide cover</figcaption></figure>

  <figure class="scene" style="grid-column:span 2"><div class="shot social">{lock(True, False, 120)}<span>Live captions and translation, on any phone</span></div>
  <figcaption>GitHub and link preview, 1280 by 640</figcaption></figure>
</div></section>

<section><h2>Sizes</h2>
  <div class="row ladder">{ladder}</div><div class="row ladder lt">{ladder}</div>
  <p class="dim">At 32 pixels and under the mark switches to a simpler drawing so it stays crisp.</p>
</section>

<section><h2>Colour versions</h2><div class="row">{variants}</div></section>

<section><h2>Files exported for A</h2><div class="scroll"><table>
<tr><th>Where it goes</th><th>Files in <code>brand/export/</code></th></tr>
<tr><td>Browser tab</td><td><code>web/favicon.svg</code>, <code>favicon.ico</code> (16, 32, 48), <code>favicon-16/32/48.png</code></td></tr>
<tr><td>Phone home screen</td><td><code>web/apple-touch-icon.png</code> (180), <code>icon-192.png</code>, <code>icon-512.png</code>, <code>icon-maskable-512.png</code>, <code>site.webmanifest</code></td></tr>
<tr><td>Pages and top bar</td><td><code>svg/relay-lockup-*.svg</code>, <code>relay-glyph-*.svg</code>, <code>relay-wordmark-*.svg</code>, <code>relay-mark.svg</code></td></tr>
<tr><td>Display panels</td><td><code>panel/splash-800x480.png</code>, <code>480x800</code>, <code>1280x800</code>, <code>800x1280</code>, <code>glyph-48/96/160.png</code></td></tr>
<tr><td>Desktop shortcuts</td><td><code>desktop/relay.icns</code> (Mac), <code>relay.ico</code> (Windows, 16 to 256), <code>relay-1024.png</code></td></tr>
<tr><td>User guide and README</td><td><code>docs/relay-lockup-on-light.png</code>, <code>relay-lockup-on-dark.png</code>, <code>relay-mark-512.png</code></td></tr>
<tr><td>GitHub and link previews</td><td><code>web/social-preview-1280x640.png</code></td></tr>
</table></div></section>
</main>
<script>
(function () {{
  var root = document.getElementById('root'), btns = document.querySelectorAll('.pick button');
  function set(c) {{ root.setAttribute('data-c', c); btns.forEach(function (b) {{ b.setAttribute('aria-pressed', String(b.dataset.c === c)); }}); try {{ localStorage.setItem('relay.logoFinal', c); }} catch (e) {{}} }}
  btns.forEach(function (b) {{ b.addEventListener('click', function () {{ set(b.dataset.c); }}); }});
  try {{ var s = localStorage.getItem('relay.logoFinal'); if (s) set(s); }} catch (e) {{}}
}})();
</script>
'''
pathlib.Path(__file__).with_name("mockups.html").write_text(page)
