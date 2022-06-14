import os
import pikepdf
import warnings

warnings.filterwarnings("error")

from pdf_mangler import mangler

# set the cwd to the tests directory
os.chdir(os.path.dirname(os.path.realpath(__file__)))


def test_integration():
    mglr = mangler.Mangler("sunny_mountain_overalls.pdf")
    mglr.mangle_pdf()
    mglr.save()
