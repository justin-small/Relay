#!/usr/bin/env python3
"""Boot animation for the display panels.  python3 brand/boot.py
The speaker rises, the caption bubble pops out, its two lines are written, then "relay" is written
beside it. Output: brand/export/panel/boot-*.gif. Needs Google Chrome and Pillow."""
import pathlib, subprocess, tempfile
from PIL import Image
import marks as M

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "export/panel"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BG = "#101010"                      # the panel's own ground (screen.cpp, kBg)
FPS, MOVE_S, HOLD_S = 20, 2.3, 1.8
N = int(FPS * MOVE_S)
STROKES = ["M" + p for p in M.WORD.split("M") if p]          # r r e l a a y y, in writing order
S = 0.62
WX, WY = 62.5 + 19 + 4.5 * S, 32 - 20 * S
LOCK_W = WX + (M.WORD_W - 4.5) * S + 2


def span(t, a, b):
    return min(1.0, max(0.0, (t - a) / (b - a)))

def ease(x):
    return 1 - (1 - x) ** 3

def back(x, k=1.9):                 # overshoots a little, then settles
    x -= 1
    return 1 + (k + 1) * x ** 3 + k * x ** 2


def frame(t):
    """The lockup at time t (0..1), as SVG elements in the lockup's 64-high grid."""
    out = []
    up = ease(span(t, 0.00, 0.20))
    if up > 0:
        h = 9 + (36.4 - 9) * up
        out.append(f'<rect x="11.5" y="{50.4 - h:.2f}" width="9" height="{h:.2f}" rx="4.5" fill="{M.FG}" opacity="{min(1, up * 4):.2f}"/>')
    pop = span(t, 0.16, 0.42)
    if pop > 0:
        k = back(pop)
        holes = ""
        for (x, y, w), (a, b) in (((30.5, 20.8, 17), (0.42, 0.54)), ((30.5, 28.3, 10.5), (0.52, 0.62))):
            cur = w * ease(span(t, a, b))
            if cur >= 4:
                holes += M.rr(x, y, round(cur, 2), 4, 2)
        out.append(f'<path transform="translate(50.4 50.2) scale({k:.3f}) translate(-50.4 -50.2)" '
                   f'fill-rule="evenodd" fill="{M.ACC}" d="{M.BUBBLE}{holes}"/>')
    mark = f'<g transform="translate(-15.3 -14.55) scale(1.4545)">{"".join(out)}</g>'
    word = ""
    for i, d in enumerate(STROKES):
        a = 0.58 + i * 0.045
        p = ease(span(t, a, a + 0.10))
        if p > 0:
            word += f'<path d="{d}" pathLength="1" stroke-dasharray="{p:.3f} 2"/>'
    word = (f'<g transform="translate({WX:.2f} {WY:.2f}) scale({S})" fill="none" stroke="{M.FG}" '
            f'stroke-width="9" stroke-linecap="round" stroke-linejoin="round">{word}</g>')
    return mark + word


def render(w, h, lock_h, name, pad=None):
    """One screenshot of every frame tiled in a grid, cut into frames, saved as a GIF."""
    lw = LOCK_W * lock_h / 64
    cols = max(1, 6400 // w); rows = -(-(N + 1) // cols)
    cells = "".join(
        f'<div style="width:{w}px;height:{h}px;display:grid;place-items:center">'
        f'<svg width="{lw:.1f}" height="{lock_h}" viewBox="0 0 {LOCK_W:.1f} 64">{frame(i / N)}</svg></div>'
        for i in range(N + 1))
    tmp = pathlib.Path(tempfile.mkdtemp()); html = tmp / "s.html"; png = tmp / "s.png"
    html.write_text(f'<body style="margin:0;background:{BG};display:grid;grid-template-columns:repeat({cols},{w}px)">{cells}</body>')
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={cols * w},{rows * h}", f"--screenshot={png}", html.as_uri()],
                   check=True, capture_output=True)
    sheet = Image.open(png).convert("RGB")
    frames = [sheet.crop(((i % cols) * w, (i // cols) * h, (i % cols) * w + w, (i // cols) * h + h)) for i in range(N + 1)]
    pal = frames[-1].quantize(colors=48, method=Image.MEDIANCUT)       # one palette, so nothing shimmers
    frames = [f.quantize(palette=pal, dither=Image.NONE) for f in frames]
    f = OUT / name
    frames[0].save(f, save_all=True, append_images=frames[1:], loop=0, optimize=False,
                   duration=[1000 // FPS] * N + [int(HOLD_S * 1000)])
    print(f"{f.relative_to(HERE)}  {w}x{h}  {len(frames)} frames  {f.stat().st_size // 1024} KB")
    return sheet, cols


OUT.mkdir(parents=True, exist_ok=True)
sheet, cols = render(800, 480, 112, "boot-800x480.gif")
render(480, 800, 84, "boot-480x800.gif")
render(1280, 800, 150, "boot-1280x800.gif")
render(800, 1280, 130, "boot-800x1280.gif")
render(448, 144, 112, "boot-logo-448x144.gif")      # the moving part alone, for the firmware to centre
# a contact strip of the landscape frames, to check the motion at a glance
pick = [0, 4, 8, 13, 18, 24, 28, 33, 38, 46]
strip = Image.new("RGB", (400 * 5, 240 * 2), BG)
for n, i in enumerate(pick):
    fr = sheet.crop(((i % cols) * 800, (i // cols) * 480, (i % cols) * 800 + 800, (i // cols) * 480 + 480)).resize((400, 240), Image.LANCZOS)
    strip.paste(fr, ((n % 5) * 400, (n // 5) * 240))
strip.save(OUT / "boot-frames.png")
