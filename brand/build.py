#!/usr/bin/env python3
"""Export every Relay logo file.  python3 brand/build.py [a|b|c]
Needs Google Chrome (rasterising) and Pillow. Output: brand/export/"""
import json, pathlib, subprocess, sys, tempfile, shutil
from PIL import Image
import marks as M

C = sys.argv[1] if len(sys.argv) > 1 else "a"
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "export"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TMP = pathlib.Path(tempfile.mkdtemp())


def raster(body, w, h, bg="transparent"):
    html = TMP / "r.html"; png = TMP / "r.png"
    html.write_text(f'<body style="margin:0;background:{bg};overflow:hidden">{body}</body>')
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1", f"--window-size={w},{h}",
                    "--default-background-color=00000000", f"--screenshot={png}", html.as_uri()],
                   check=True, capture_output=True)
    return Image.open(png).convert("RGBA").crop((0, 0, w, h))


def write(rel, data):
    f = OUT / rel; f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(data) if isinstance(data, str) else data.save(f)
    return f


if OUT.exists(): shutil.rmtree(OUT)
mono = lambda col: M.glyph(C, 64, col, col)

# --- vector -----------------------------------------------------------------
write("svg/relay-mark.svg", M.tile(C))
write("svg/relay-mark-small.svg", M.tile(C, small=True))          # 32px and under
write("svg/relay-glyph-on-dark.svg", M.glyph(C, 64, M.FG, M.ACC))
write("svg/relay-glyph-on-light.svg", M.glyph(C, 64, M.INK, M.ACC_L))
write("svg/relay-glyph-mono-white.svg", mono("#ffffff"))
write("svg/relay-glyph-mono-black.svg", mono("#000000"))
write("svg/relay-glyph-currentcolor.svg", mono("currentColor"))
for name, col in (("on-dark", M.FG), ("on-light", M.INK)):
    write(f"svg/relay-wordmark-{name}.svg",
          M.svg(M.WORD_W, 93, M.wordmark(col), f"-4.5 {M.WORD_TOP} {M.WORD_W} 93"))
write("svg/relay-lockup-on-dark.svg", M.lockup(C, True, False))
write("svg/relay-lockup-on-light.svg", M.lockup(C, False, False))
write("svg/relay-lockup-tile-on-dark.svg", M.lockup(C, True, True))
write("svg/relay-lockup-tile-on-light.svg", M.lockup(C, False, True))

# --- masters ----------------------------------------------------------------
big = raster(M.tile(C, 1024), 1024, 1024)                 # rounded tile, transparent corners
big_small = raster(M.tile(C, 1024, small=True), 1024, 1024)
square = raster(M.tile(C, 1024, radius=0), 1024, 1024)    # full bleed, the OS rounds it
fit = lambda im, n: im.resize((n, n), Image.LANCZOS)

# --- web --------------------------------------------------------------------
write("web/favicon.svg", M.tile(C, small=True))
for n in (16, 32, 48):
    write(f"web/favicon-{n}.png", fit(big_small, n))
fit(big_small, 48).save(OUT / "web/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
write("web/apple-touch-icon.png", fit(square, 180).convert("RGB"))
write("web/icon-192.png", fit(big, 192))
write("web/icon-512.png", fit(big, 512))
write("web/icon-maskable-512.png", fit(square, 512).convert("RGB"))
write("web/site.webmanifest", json.dumps({
    "name": "Relay", "short_name": "Relay", "display": "browser",
    "background_color": "#000000", "theme_color": "#000000",
    "icons": [{"src": "/static/brand/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/static/brand/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "/static/brand/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
               "purpose": "maskable"}]}, indent=2) + "\n")
og = (f'<div style="width:1280px;height:640px;background:#000;display:grid;place-items:center">'
      f'<div style="text-align:center">{M.lockup(C, True, False, 150)}'
      f'<div style="font:500 30px -apple-system,system-ui,sans-serif;color:#8b8f99;margin-top:38px">'
      f'Live captions and translation, on any phone</div></div></div>')
write("web/social-preview-1280x640.png", raster(og, 1280, 640).convert("RGB"))

# --- desktop ----------------------------------------------------------------
write("desktop/relay-1024.png", big)
fit(big, 256).save(OUT / "desktop/relay.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
mac = Image.new("RGBA", (1024, 1024)); mac.alpha_composite(fit(big, 824), (100, 100))
iconset = TMP / "relay.iconset"; iconset.mkdir()
for n in (16, 32, 128, 256, 512):
    fit(mac, n).save(iconset / f"icon_{n}x{n}.png"); fit(mac, n * 2).save(iconset / f"icon_{n}x{n}@2x.png")
subprocess.run(["iconutil", "-c", "icns", iconset, "-o", OUT / "desktop/relay.icns"], check=True)

# --- display panels (black, as the firmware draws) ---------------------------
for w, h, lh in ((800, 480, 112), (480, 800, 84), (1280, 800, 150), (800, 1280, 130)):
    body = f'<div style="width:{w}px;height:{h}px;background:#000;display:grid;place-items:center">{M.lockup(C, True, False, lh)}</div>'
    write(f"panel/splash-{w}x{h}.png", raster(body, w, h).convert("RGB"))
for n in (48, 96, 160):
    g = raster(M.glyph(C, n * 4, M.FG, M.ACC), n * 4, n * 4)
    bg = Image.new("RGBA", g.size, "#000"); bg.alpha_composite(g)
    write(f"panel/glyph-{n}.png", fit(bg, n).convert("RGB"))

# --- documents ---------------------------------------------------------------
for name, dark in (("on-light", False), ("on-dark", True)):
    svgs = M.lockup(C, dark, False, 256)
    w = int(svgs.split('width="')[1].split('"')[0])
    write(f"docs/relay-lockup-{name}.png", raster(svgs, w, 256))
write("docs/relay-mark-512.png", fit(big, 512))

# --- into the software ---------------------------------------------------------
ROOT = HERE.parent
static = ROOT / "app/static/brand"; static.mkdir(exist_ok=True)
for name in ("favicon.svg", "favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png",
             "icon-maskable-512.png", "site.webmanifest"):
    shutil.copy(OUT / "web" / name, static / name)
shutil.copy(OUT / "svg/relay-mark.svg", static / "relay-mark.svg")
# The lockup the pages draw inline: it takes the page's text colour and its accent, so it follows the theme.
inline = M.lockup(C, True, False, 24, colors=("currentColor", "var(--accent)"))
inline = inline.replace("<svg ", '<svg class="lockup" role="img" aria-label="Relay" ', 1)
(ROOT / "app/templates/_lockup.svg").write_text(inline + "\n")
shutil.copy(OUT / "svg/relay-lockup-on-light.svg", ROOT / "docs/guide/images/relay-lockup.svg")

shutil.rmtree(TMP)
for f in sorted(OUT.rglob("*")):
    if f.is_file(): print(f"{f.relative_to(HERE)}  {f.stat().st_size}")
