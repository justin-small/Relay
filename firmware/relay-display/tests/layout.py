"""Where the panel's controls are, in logical pixels, for the UI scale in `main/ui_scale.h`.

The firmware writes every size as `ui_px(<size at 100%>)`; this is the same rounding, with the scale read from the
header, so the tests' tap positions follow the scale. The numbers passed in are the sizes at 100%.
"""

import re
from pathlib import Path

_header = (Path(__file__).resolve().parent.parent / "main" / "ui_scale.h").read_text()
SCALE_PCT = int(re.search(r"#define RELAY_UI_SCALE_PCT (\d+)", _header).group(1))


def ui_px(at_100: int) -> int:
    return (at_100 * SCALE_PCT + 50) // 100


# The 800 x 480 panel.
WIDTH, HEIGHT = 800, 480

HEADER_H = ui_px(56)

# The jump button: centred, `ui_px(20)` above the bottom edge.
JUMP_W, JUMP_H = ui_px(280), ui_px(64)
JUMP_X, JUMP_Y = WIDTH // 2, HEIGHT - ui_px(20) - JUMP_H // 2

# The menu button, at the top right, and the card it opens (centred, with its own padding and gaps).
MENU_W, MENU_H = ui_px(64), ui_px(44)
MENU_BUTTON = (WIDTH - ui_px(12) - MENU_W // 2, (HEADER_H - MENU_H) // 2 + MENU_H // 2)
CARD_W, CARD_H = ui_px(620), ui_px(440)
_card_x, _card_y = (WIDTH - CARD_W) // 2, (HEIGHT - CARD_H) // 2
_pad, _gap = ui_px(16), ui_px(10)
_title_h, _mode_h, _tab_h, _row_h, _row_gap = ui_px(48), ui_px(52), ui_px(44), ui_px(56), ui_px(8)
_title_y = _card_y + _pad
_mode_y = _title_y + _title_h + _gap
_tabs_y = _mode_y + _mode_h + _gap
DONE = (_card_x + CARD_W - _pad - ui_px(110) // 2, _title_y + _title_h // 2)
_mode_w, _tab_w, _col_gap = ui_px(200), ui_px(140), ui_px(12)
ONE_FEED = (_card_x + _pad + _mode_w // 2, _mode_y + _mode_h // 2)
TWO_FEEDS = (_card_x + _pad + _mode_w + _col_gap + _mode_w // 2, ONE_FEED[1])
LEFT_TAB = (_card_x + _pad + _tab_w // 2, _tabs_y + _tab_h // 2)
RIGHT_TAB = (_card_x + _pad + _tab_w + _col_gap + _tab_w // 2, LEFT_TAB[1])
_list_one = _mode_y + _mode_h + _gap
_list_split = _tabs_y + _tab_h + _gap


def feed_row(index: int, *, split: bool) -> tuple[int, int]:
    """The centre of the menu's `index`th feed."""
    return WIDTH // 2, (_list_split if split else _list_one) + _row_h // 2 + (_row_h + _row_gap) * index


# The notice under the header (a language that is not live) and the status dot's row.
NOTICE_Y = HEADER_H + ui_px(32) // 2
DOT_Y = HEADER_H // 2

# The take-control confirmation: centred; its "Take control" button at the bottom left.
CONFIRM_YES = (
    (WIDTH - ui_px(600)) // 2 + ui_px(30) + ui_px(250) // 2,
    (HEIGHT + ui_px(220)) // 2 - ui_px(24) - ui_px(72) // 2,
)
