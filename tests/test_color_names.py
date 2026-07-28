"""
Covers ui/color_names.closest_color_name -- the hex-to-name lookup the
Change History dialog uses to show "Purple" instead of "#9B59B6". Matches
against this app's own six-swatch highlight vocabulary (see
ui/Pages/CurrentSheet.HIGHLIGHT_PALETTE, which color_names.NAMED_COLORS
duplicates the hex values of), not a generic colour-name list, since a
row_color is always one of those six or None. Display-only: nothing here
reads or writes stored data.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.color_names import closest_color_name, NAMED_COLORS


class ClosestColorNameTests(unittest.TestCase):
    def test_matches_the_current_sheet_highlight_palette_exactly(self):
        """Keeps color_names.NAMED_COLORS honest against
        ui.Pages.CurrentSheet.HIGHLIGHT_PALETTE, which it deliberately
        duplicates rather than imports (see color_names.py's docstring
        for why) -- this is what would catch the two drifting apart."""
        from ui.Pages.CurrentSheet import HIGHLIGHT_PALETTE

        palette_colors = {label: hexval for label, hexval in HIGHLIGHT_PALETTE if hexval}
        self.assertEqual(NAMED_COLORS, palette_colors)

    def test_exact_named_hex_values_resolve_to_their_own_name(self):
        for name, hex_value in NAMED_COLORS.items():
            self.assertEqual(closest_color_name(hex_value), name)

    def test_a_nearby_but_not_exact_hex_resolves_to_the_nearest_name(self):
        # One RGB unit off Blue (#4A90D9) in each channel -- not an exact
        # match, but unambiguously closer to Blue than to anything else
        # (the six colours are >90 RGB-units apart from each other).
        self.assertEqual(closest_color_name("#4B91DA"), "Blue")

    def test_case_and_missing_hash_are_both_accepted(self):
        self.assertEqual(closest_color_name("e05252"), closest_color_name("#E05252"))
        self.assertEqual(closest_color_name("#e05252"), "Red")

    def test_a_color_far_from_every_named_color_falls_back_to_none(self):
        # Black: ~130+ RGB-units from the nearest of the six (Purple),
        # comfortably past the default 40 threshold.
        self.assertIsNone(closest_color_name("#000000"))

    def test_invalid_input_returns_none_without_raising(self):
        for bad in (None, "", "not-a-color", "#12", "#GGGGGG", 12345):
            self.assertIsNone(closest_color_name(bad))

    def test_threshold_is_configurable_per_call(self):
        # A very small threshold should reject even a genuinely close match.
        self.assertIsNone(closest_color_name("#4B91DA", max_distance=1))
        # A very large one should accept almost anything.
        self.assertIsNotNone(closest_color_name("#000000", max_distance=1000))


if __name__ == "__main__":
    unittest.main()
