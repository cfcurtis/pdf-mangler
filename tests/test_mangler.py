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


def test_save_path():
    mglr = mangler.Mangler("sample-sigconf.pdf")
    mglr.mangle_pdf()
    mglr.save()
    assert os.path.exists(mglr.hash_name)

    mglr.save("..")
    assert os.path.exists(Path("..") / mglr.hash_name)


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


def test_config():
    mglr = mangler.Mangler()
    assert isinstance(mglr.config("mangle"), dict)
    assert mglr.config("mangle", "metadata") == True
    assert mglr.config("mangle", "nonexistant") is None


def test_png_image():
    mglr = mangler.Mangler("chalk_drawing.pdf")
    mglr.mangle_pdf()
    mglr.save()
    assert os.path.exists(mglr.hash_name)


def test_tweak_num(fake_doc):
    mglr = mangler.Mangler(pdf=fake_doc)

    for width in [1, 3, 4, 5]:
        # positive integer
        newb = mglr.tweak_num(1, 18, width)
        assert len(newb) == width
        # zero
        newb = mglr.tweak_num(0, 18, width)
        assert len(newb) == width
        # negative integer
        newb = mglr.tweak_num(-1, 18, width)

    for width in range(3, 10):
        for sign in [-1, 1]:
            # 0.333
            newb = mglr.tweak_num(sign * 1 / 3, 18, width)
            assert len(newb) == width

            # more digits
            newb = mglr.tweak_num(sign * 100 / 3, 18, width)
            assert len(newb) == width

    # specific troublesome number
    newb = mglr.tweak_num(-2.386, 18, 7)
    assert len(newb) == 7


def test_mangle_stream(fake_doc):
    mglr = mangler.Mangler(pdf=fake_doc)
    newstream = mglr.mangle_stream(fake_doc, None)
    assert len(newstream.split()) == len(fake_doc.commands.split())
    assert len(newstream) == len(fake_doc.commands)


class FakePDF:
    def __init__(self):
        self.trailer = {"/ID": [b"<1234567890>", b"<0987654321>"]}
        with open(Path(__file__).parent / "test_stream", "rb") as f:
            self.commands = f.read()

    def get_stream_buffer(self):
        return self.commands


@pytest.fixture
def fake_doc():
    return FakePDF()
