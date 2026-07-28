"""
Hex-to-name lookup for display purposes only -- turns a stored hex colour
like "#9B59B6" into a human-readable name like "Purple" wherever a UI
wants to show one. Nothing here writes or normalizes stored data: the
actual hex string in the database (e.g. current_sheet.row_color,
change_log.old_value/new_value) is untouched by this module.

Matches against this app's own highlight vocabulary (the six swatches in
ui/Pages/CurrentSheet.HIGHLIGHT_PALETTE, which is where every row_color
value actually comes from -- picked locally from that exact menu, or
merged in from another device's own use of the same fixed palette) rather
than a large generic colour-name list: a row_color is always one of these
six or None, so this is what the values actually mean here. Duplicated as
its own constant, not imported from ui.Pages.CurrentSheet, to avoid a
circular import (CurrentSheet -> change_log_dialog -> color_names would
become CurrentSheet -> change_log_dialog -> color_names -> CurrentSheet).
Keep this in sync with HIGHLIGHT_PALETTE's hex values if that ever changes.
"""

import re

# Display name -> "#RRGGBB", the exact hex values from
# ui.Pages.CurrentSheet.HIGHLIGHT_PALETTE (its "None" entry excluded --
# an unset colour is handled separately, as _EMPTY_VALUE_TEXT, not as a
# name here).
NAMED_COLORS = {
    "Red": "#E05252",
    "Orange": "#FF7A00",
    "Yellow": "#FFEE33",
    "Green": "#4CAF50",
    "Blue": "#4A90D9",
    "Purple": "#9B59B6",
}

_HEX_PATTERN = re.compile(r"^#?([0-9A-Fa-f]{6})$")

# Chosen empirically (see tests/test_color_names.py): every stored
# row_color is either an exact match (0 distance) or None, since these six
# hex values are the only colours the app's own picker ever writes -- this
# just guards against showing a misleading name for some other hex value
# that might reach this code some other way (e.g. old data, a future
# picker change). Max possible RGB Euclidean distance is ~441 (black to
# white); 40 is well inside the ~140+ gap between any two of these six
# colours, so a genuine near-miss still resolves while an unrelated hex
# correctly falls back to raw hex instead of being mislabeled.
DEFAULT_MAX_DISTANCE = 40.0


def _hex_to_rgb(hex_color):
    if not isinstance(hex_color, str):
        return None
    match = _HEX_PATTERN.match(hex_color.strip())
    if not match:
        return None
    value = match.group(1)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def closest_color_name(hex_color, max_distance=DEFAULT_MAX_DISTANCE):
    """
    The name (one of NAMED_COLORS) whose RGB value is nearest hex_color by
    Euclidean distance, or None if hex_color doesn't parse as a hex
    colour, or the nearest name is still farther than max_distance away
    (i.e. it doesn't actually resemble any of this app's six highlight
    colours -- callers should show the raw hex instead of forcing a
    misleading name onto it).
    """
    rgb = _hex_to_rgb(hex_color)
    if rgb is None:
        return None

    best_name, best_distance = None, None
    for name, named_hex in NAMED_COLORS.items():
        named_rgb = _hex_to_rgb(named_hex)
        distance = sum((a - b) ** 2 for a, b in zip(rgb, named_rgb)) ** 0.5
        if best_distance is None or distance < best_distance:
            best_name, best_distance = name, distance

    if best_distance is None or best_distance > max_distance:
        return None
    return best_name
