import unittest
from pdf_mangler import text


class TestText(unittest.TestCase):
    def test_map_charset(self):
        charset = "/a/s/d/f/J/K/L/M/colon/emdash/zero/one/two"
        char_cats = text.map_charset(charset)
        self.assertEqual(
            char_cats,
            {
                "Ll": "asdf",
                "Lu": "JKLM",
                "Nd": "012",
            },
        )

    def test_map_numeric_range(self):
        self.assertEqual(text.map_numeric_range(48, 67), {"Nd": "0123456789", "Lu": "ABC"})
