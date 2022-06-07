import unittest

from mangler import mangler


class TestMangler(unittest.TestCase):
    def test_integration(self):
        mangler.main(
            "tests/sunny_mountain_overalls.pdf", "tests/sunny_mountain_overalls_mangled.pdf"
        )
