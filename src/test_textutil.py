"""AC-D1-SLUGIFY and AC-D1-TESTS."""
from __future__ import annotations

import unittest

from textutil import slugify


class SlugifyTests(unittest.TestCase):
    def test_punctuation_collapses_to_single_hyphens(self):
        self.assertEqual("scope-exclusions-preserve", slugify("Scope, Exclusions & Preserve"))

    def test_surrounding_and_repeated_whitespace_is_dropped(self):
        self.assertEqual("a-b", slugify("  A  B  "))

    def test_leading_and_trailing_separators_are_stripped(self):
        self.assertEqual("x", slugify("--x--"))


if __name__ == "__main__":
    unittest.main()
