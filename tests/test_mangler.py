import unittest
import os
import warnings

warnings.filterwarnings("error")

from pdf_mangler import mangler

# set the cwd to the tests directory
os.chdir(os.path.dirname(os.path.realpath(__file__)))


class TestMangler(unittest.TestCase):
    def test_integration(self):
        mangler.main("sunny_mountain_overalls.pdf")
