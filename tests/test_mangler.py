import os
from pathlib import Path
import pytest
import warnings

warnings.filterwarnings("error")

from pdf_mangler import mangler

# set the cwd to the tests directory
os.chdir(Path(__file__).parent)


def test_hash_name():
    mglr = mangler.Mangler("sunny_mountain_overalls.pdf")
    assert mglr.hash_name == "82592449b584bd80e2edbb7c6cc1b282.pdf"

    mglr.filename = "sample-sigconf.pdf"
    assert mglr.hash_name == "610f592b04350db727f5a24f37342262.pdf"


def test_reuse_object():
    mglr = mangler.Mangler("sunny_mountain_overalls.pdf")
    mglr.mangle_pdf()
    mglr.save()
    assert os.path.exists(mglr.hash_name)

    mglr.filename = "sample-sigconf.pdf"
    mglr.mangle_pdf()
    mglr.save()
    assert os.path.exists(mglr.hash_name)


def test_nonexistant_filename():
    with pytest.raises(FileNotFoundError):
        mglr = mangler.Mangler("nonexistant_file.pdf")


def test_javascript():
    # javascript.pdf from https://github.com/openpreserve/format-corpus/tree/master/pdfCabinetOfHorrors
    mglr = mangler.Mangler("javascript.pdf")
    mglr.mangle_pdf()
    mglr.save()
    assert os.path.exists(mglr.hash_name)
