"""Relay logo geometry. Every mark is drawn on a 64x64 grid; the wordmark is
monoline strokes, so no font is needed anywhere."""

TILE, FG, ACC = "#14161a", "#f4f4f5", "#4ade80"      # dark surfaces
INK, ACC_L = "#18181b", "#15803d"                    # light surfaces


def rr(x, y, w, h, r):
    return (f"M{x+r} {y}H{x+w-r}A{r} {r} 0 0 1 {x+w} {y+r}V{y+h-r}A{r} {r} 0 0 1 {x+w-r} {y+h}"
            f"H{x+r}A{r} {r} 0 0 1 {x} {y+h-r}V{y+r}A{r} {r} 0 0 1 {x+r} {y}Z")


# The caption bubble: 29 wide by 25 tall, with a short tail to the baseline (the R's leg).
BUBBLE = ("M33.5 14H44.5A9 9 0 0 1 53.5 23V30A9 9 0 0 1 49 37.8L52.6 48.2"
          "Q53.6 51.4 50.4 50.2L37 39H33.5A9 9 0 0 1 24.5 30V23A9 9 0 0 1 33.5 14Z")


def mark_a(fg, acc, small=False):
    """Bubble R: the stem is the speaker, the bowl and leg are a caption bubble."""
    holes = rr(31, 22.2, 16, 8.4, 4.2) if small else rr(30.5, 20.8, 17, 4, 2) + rr(30.5, 28.3, 10.5, 4, 2)
    return (f'<rect x="11.5" y="14" width="9" height="36.4" rx="4.5" fill="{fg}"/>'
            f'<path fill-rule="evenodd" fill="{acc}" d="{BUBBLE}{holes}"/>')


def mark_b(fg, acc, small=False):
    """Live line: two settled caption lines and the one being written."""
    return (f'<rect x="13" y="15.5" width="38" height="7.5" rx="3.75" fill="{fg}"/>'
            f'<rect x="13" y="28.25" width="29" height="7.5" rx="3.75" fill="{fg}"/>'
            f'<rect x="13" y="41" width="17" height="7.5" rx="3.75" fill="{acc}"/>'
            f'<rect x="34.5" y="38.5" width="5" height="12.5" rx="2.5" fill="{acc}"/>')


def mark_c(fg, acc, small=False, gap=TILE):
    """Handoff: what was said, passed on as what is read."""
    back = rr(11, 11, 30, 23, 7)
    front = ("M31 25H46A7 7 0 0 1 53 32V40A7 7 0 0 1 48.6 46.5L50.6 52.6Q51 54.6 49 53.6"
             "L41 47H31A7 7 0 0 1 24 40V32A7 7 0 0 1 31 25Z")
    return (f'<path d="{back}M17 34L15 41.5L24 34" fill="none" stroke="{fg}" stroke-width="4.4" stroke-linejoin="round"/>'
            f'<path d="{front}" fill="{acc}" stroke="{gap}" stroke-width="3.2" stroke-linejoin="round" paint-order="stroke"/>')


def circ(cx, cy, r):
    return f"M{cx-r} {cy}a{r} {r} 0 1 0 {2*r} 0a{r} {r} 0 1 0 {-2*r} 0Z"


def mark_a1(fg, acc, small=False):
    """Monoline: the same R drawn as an outline, in the wordmark's own stroke."""
    w = 6.4 if small else 5.4
    out = ("M34.5 16.7H43A8 8 0 0 1 51 24.7V29A8 8 0 0 1 46.6 36.2L51 47.3"
           "L37 37H34.5A8 8 0 0 1 26.5 29V24.7A8 8 0 0 1 34.5 16.7Z")
    line = "" if small else f'<path d="M33.4 26.9H44.1" stroke="{acc}" stroke-width="3.8" stroke-linecap="round"/>'
    return (f'<path d="M14.6 16.7V47.3" stroke="{fg}" stroke-width="{w}" stroke-linecap="round"/>'
            f'<path d="{out}" fill="none" stroke="{acc}" stroke-width="{w}" stroke-linejoin="round"/>{line}')


def mark_a2(fg, acc, small=False):
    """Joined: upright and bubble touch, so it reads as one solid two-tone R."""
    bubble = ("M18 14H44.5A9 9 0 0 1 53.5 23V30A9 9 0 0 1 49 37.8L52.6 48.2"
              "Q53.6 51.4 50.4 50.2L37 39H18Z")
    return (f'<path fill-rule="evenodd" fill="{acc}" d="{bubble}{rr(28, 22.2, 18, 8.4, 4.2)}"/>'
            f'<rect x="10.5" y="14" width="10.5" height="36.4" rx="5.25" fill="{fg}"/>')


def mark_a3(fg, acc, small=False):
    """Speaker: the upright becomes a person, and the bubble shows speech arriving."""
    holes = rr(31, 22.2, 16, 8.4, 4.2) if small else circ(32.4, 26.5, 2.5) + circ(39, 26.5, 2.5) + circ(45.6, 26.5, 2.5)
    return (f'<circle cx="16" cy="19.2" r="5.2" fill="{fg}"/>'
            f'<rect x="11.5" y="27.4" width="9" height="23" rx="4.5" fill="{fg}"/>'
            f'<path fill-rule="evenodd" fill="{acc}" d="{BUBBLE}{holes}"/>')


MARKS = {"a": mark_a, "a1": mark_a1, "a2": mark_a2, "a3": mark_a3, "b": mark_b, "c": mark_c}
NAMES = {"a": "Bubble R", "a1": "Monoline", "a2": "Joined", "a3": "Speaker", "b": "Live line", "c": "Handoff"}

# Wordmark "relay": x-height 40, stroke 9. Ink box x -4.5..214.5, y -28.5..64.5
WORD_W, WORD_TOP, WORD_BOT = 219, -28.5, 64.5
WORD = ("M0 40V0M0 18A18 18 0 0 1 18 0"                       # r
        "M33 20H73A20 20 0 1 0 67.14 34.14"                   # e
        "M92 -24V40"                                          # l
        "M151 20A20 20 0 1 0 151 20.01M151 0V40"              # a
        "M170 0V20A20 20 0 0 0 210 20M210 0V44A16 16 0 0 1 194 60H186")  # y


def wordmark(color, x=0, y=0, s=1.0):
    return (f'<path transform="translate({x} {y}) scale({s})" d="{WORD}" fill="none" stroke="{color}" '
            f'stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>')


def svg(w, h, body, vb=None):
    vb = vb or f"0 0 {w} {h}"
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="{vb}">{body}</svg>'


def tile(concept, size=64, small=False, radius=14, bg=TILE, fg=FG, acc=ACC):
    body = f'<rect width="64" height="64" rx="{radius}" fill="{bg}"/>' + MARKS[concept](fg, acc, small)
    return svg(size, size, body, "0 0 64 64")


def glyph(concept, size=64, fg=FG, acc=ACC, small=False):
    """Mark with no tile, cropped to its ink (grid 10..54)."""
    kw = {"gap": "none"} if concept == "c" else {}
    return svg(size, size, MARKS[concept](fg, acc, small, **kw), "10 10 44 44")


def lockup(concept, dark=True, tiled=True, h=64, colors=None):
    fg, acc = colors or ((FG, ACC) if dark else (INK, ACC_L))
    s = 0.62                                    # x-height 24.8 beside a 64 tile
    wy = 32 - 20 * s                            # x-height centred on the mark
    if tiled:
        m = f'<rect width="64" height="64" rx="14" fill="{TILE}"/>' + MARKS[concept](FG, ACC)
        wx = 64 + 20 + 4.5 * s
    else:                                       # bare mark, grown to fill the height
        kw = {"gap": "#000" if dark else "#fff"} if concept == "c" else {}
        m = f'<g transform="translate(-15.3 -14.55) scale(1.4545)">{MARKS[concept](fg, acc, **kw)}</g>'
        wx = 62.5 + 19 + 4.5 * s
    w = wx + (WORD_W - 4.5) * s + 2
    return svg(round(w * h / 64), h, m + wordmark(fg, wx, wy, s), f"0 0 {w:.1f} 64")
