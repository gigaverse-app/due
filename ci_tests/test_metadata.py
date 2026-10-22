"""Check real UTF-8 descriptions without importing Due or its services."""

import unittest

from scripts.ci.check_metadata import check_metadata


class MetadataTests(unittest.TestCase):
    def test_utf8_description_round_trips(self):
        readme = "# Due\n\nREADME → wheel · sdist\n"
        raw = f"Name: django-due\nDescription-Content-Type: text/markdown\n\n{readme}".encode()
        check_metadata(raw, readme)

    def test_stale_description_fails(self):
        raw = b"Name: django-due\nDescription-Content-Type: text/markdown\n\nOld README\n"
        with self.assertRaisesRegex(AssertionError, "description differs"):
            check_metadata(raw, "Current README")
