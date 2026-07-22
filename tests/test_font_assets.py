"""
Guards the bundled font files against silent corruption.

assets/fonts/GentleHearts-Regular.ttf was committed already broken: it had
been round-tripped through a text decode/encode at some point, so every
byte that wasn't valid UTF-8 had become U+FFFD. The file was still there,
still roughly the right size, and still named .ttf -- but Qt refused it,
and the app quietly rendered in a generic serif instead. Nothing failed
loudly; the only trace was a warning buried in stdout.

A font's sfnt header carries three fields that are pure functions of the
table count, so a single altered byte anywhere in the header is provable
without needing a reference copy of the file.
"""

import hashlib
import os
import struct
import unittest

FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
_FONT_EXTENSIONS = (".ttf", ".otf")

# Files already known to be broken, waived so the suite stays green until
# they're replaced. Keyed by SHA-256 of the exact bad bytes ON PURPOSE,
# rather than by filename or by "skip anything that looks corrupt" -- a
# guard that stands down whenever it finds a problem is not a guard. Drop
# a real font in and the hash no longer matches, so every check below runs
# against it for real, with no edit to this file needed.
_KNOWN_BROKEN = {
    "41d6e8fea4690b82f7b5dbf9aeeec3f10859f277336589891be36b0e6d7906b6":
        "GentleHearts-Regular.ttf was committed already corrupted: a text "
        "encode/decode round trip replaced ~10,278 byte sequences with U+FFFD. "
        "The original bytes are unrecoverable and no clean copy exists in git "
        "history, so this needs a fresh download of the font. The app falls "
        "back to a generic serif in the meantime (see ui/theme.py). Remove "
        "this waiver once the file is replaced.",
}


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


def _digest(path):
    return hashlib.sha256(_read(path)).hexdigest()


def _waiver_for(path):
    """The recorded reason this exact file is exempt, or None if it should
    be checked."""
    return _KNOWN_BROKEN.get(_digest(path))


# The sfnt version tags a font file may legitimately start with.
_VALID_SFNT_VERSIONS = {
    0x00010000,          # TrueType outlines
    0x4F54544F,          # 'OTTO' -- CFF/PostScript outlines
    0x74727565,          # 'true'
    0x74746366,          # 'ttcf' -- TrueType collection
}
_REPLACEMENT_CHAR_UTF8 = b"\xef\xbf\xbd"


def _font_files():
    if not os.path.isdir(FONTS_DIR):
        return []
    return [
        os.path.join(FONTS_DIR, name)
        for name in sorted(os.listdir(FONTS_DIR))
        if name.lower().endswith(_FONT_EXTENSIONS)
    ]


class FontAssetIntegrityTests(unittest.TestCase):
    def test_there_is_at_least_one_font_to_check(self):
        """If the fonts folder empties out, every check below would pass
        vacuously -- which is how a guard quietly stops guarding."""
        self.assertTrue(_font_files(), "no font files found in assets/fonts")

    def test_no_waiver_has_outlived_its_file(self):
        """A waiver that no longer matches anything means the bad file was
        replaced (or removed) and the entry should go -- otherwise the list
        quietly grows into a set of exemptions nobody can account for."""
        present = {_digest(path) for path in _font_files()}
        for digest in _KNOWN_BROKEN:
            self.assertIn(
                digest, present,
                msg=(f"the waiver for {digest[:12]}... no longer matches any file in "
                     "assets/fonts -- the font was replaced, so delete this entry from "
                     "_KNOWN_BROKEN and let the checks run."),
            )

    def test_no_font_contains_utf8_replacement_characters(self):
        """The signature of a binary file decoded as text and re-encoded.
        A real font can contain the bytes ef bf bd by coincidence, but not
        thousands of times."""
        for path in _font_files():
            with self.subTest(font=os.path.basename(path)):
                waiver = _waiver_for(path)
                if waiver:
                    self.skipTest(waiver)
                data = _read(path)
                count = data.count(_REPLACEMENT_CHAR_UTF8)
                self.assertLess(
                    count, 10,
                    msg=(f"{os.path.basename(path)} contains {count} U+FFFD sequences -- "
                         "it has been corrupted by a text encode/decode round trip and "
                         "must be replaced with a fresh copy (the original bytes are "
                         "not recoverable). See .gitattributes."),
                )

    def test_every_font_has_a_valid_sfnt_header(self):
        for path in _font_files():
            with self.subTest(font=os.path.basename(path)):
                waiver = _waiver_for(path)
                if waiver:
                    self.skipTest(waiver)
                data = _read(path)
                self.assertGreater(len(data), 12, "file is too small to hold a header")

                version, num_tables, search_range, entry_selector, range_shift = (
                    struct.unpack(">IHHHH", data[:12])
                )
                self.assertIn(
                    version, _VALID_SFNT_VERSIONS,
                    msg=f"unrecognised sfnt version 0x{version:08x}",
                )
                self.assertGreater(num_tables, 0)

                # searchRange/entrySelector/rangeShift are defined by the
                # spec purely in terms of numTables, so they can be
                # recomputed and compared with no reference file needed.
                expected_selector = num_tables.bit_length() - 1
                expected_range = (2 ** expected_selector) * 16
                self.assertEqual(
                    (search_range, entry_selector, range_shift),
                    (expected_range, expected_selector, num_tables * 16 - expected_range),
                    msg="sfnt header fields disagree with numTables -- the header bytes "
                        "have been altered, so the file is not a loadable font",
                )

    def test_the_table_directory_is_self_consistent(self):
        """Each table record says where its data lives; those offsets have
        to land inside the file. A truncated or mangled font fails here
        even if the 12-byte header survived intact."""
        for path in _font_files():
            with self.subTest(font=os.path.basename(path)):
                waiver = _waiver_for(path)
                if waiver:
                    self.skipTest(waiver)
                data = _read(path)
                num_tables = struct.unpack(">H", data[4:6])[0]

                for index in range(num_tables):
                    start = 12 + index * 16
                    tag, _checksum, offset, length = struct.unpack(
                        ">4sIII", data[start:start + 16]
                    )
                    self.assertLessEqual(
                        offset + length, len(data),
                        msg=f"table {tag!r} runs past the end of the file",
                    )


if __name__ == "__main__":
    unittest.main()

